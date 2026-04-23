# -*- coding: utf-8 -*-

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import admin.routes as admin_routes
from app import app as flask_app
from config_app import db
from models import StaffAuditLog, StaffUser


def _login(client, usuario="Cruz", clave="8998"):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _async_headers():
    return {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Admin-Async": "1",
    }


def test_monitoreo_dashboard_shell_fallback_clasico_intacto():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    resp = client.get("/admin/monitoreo", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="monitoreoDashboardShellAsyncRegion"' in html
    assert "Monitoreo de Secretarias" in html
    assert "Buscar candidata (nombre/cédula/código)" in html
    assert 'id="metricsCards"' in html


def test_monitoreo_dashboard_shell_get_async_devuelve_region_y_target():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    resp = client.get("/admin/monitoreo", headers=_async_headers(), follow_redirects=False)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("success") is True
    assert isinstance(payload.get("message"), str)
    assert isinstance(payload.get("category"), str)
    assert payload.get("update_target") == "#monitoreoDashboardShellAsyncRegion"
    assert payload.get("redirect_url") == "/admin/monitoreo"
    html = payload.get("replace_html") or ""
    assert "Monitoreo de Secretarias" in html
    assert "Buscar candidata (nombre/cédula/código)" in html
    assert "Ver logs completos" in html


def test_monitoreo_dashboard_alertas_region_no_regresion_obvia():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    resp = client.get("/admin/monitoreo", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="monitoreoAlertsAsyncRegion"' in html
    assert "Alertas críticas (últimas 10)" in html
    assert 'data-async-target="#monitoreoAlertsAsyncRegion"' in html


def test_monitoreo_dashboard_reduce_activity_ranking_recomputation():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    calls = []

    def _fake_activity_ranking(since_dt, until_dt=None, only_secretarias=False):
        calls.append({"only_secretarias": bool(only_secretarias), "until_dt": until_dt})
        return []

    with patch.object(admin_routes, "_activity_ranking", side_effect=_fake_activity_ranking):
        resp = client.get("/admin/monitoreo", follow_redirects=False)

    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0]["only_secretarias"] is True


def test_monitoreo_candidatas_search_get_async_devuelve_region():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    fake_rows = [
        SimpleNamespace(fila=901, codigo="SEA-901", nombre_completo="Ana Search", cedula="001-1234567-1", estado="lista_para_trabajar"),
    ]
    query_mock = SimpleNamespace(
        filter=lambda *args, **kwargs: SimpleNamespace(
            order_by=lambda *a, **k: SimpleNamespace(limit=lambda *la, **lk: SimpleNamespace(all=lambda: fake_rows))
        )
    )
    fake_model = SimpleNamespace(
        query=query_mock,
        nombre_completo=SimpleNamespace(ilike=lambda *_: None),
        cedula=SimpleNamespace(ilike=lambda *_: None),
        codigo=SimpleNamespace(ilike=lambda *_: None),
        fila=SimpleNamespace(desc=lambda: None),
        cedula_norm_digits=SimpleNamespace(ilike=lambda *_: None),
    )
    with patch.object(admin_routes, "Candidata", fake_model), \
         patch.object(admin_routes, "cast", lambda *args, **kwargs: SimpleNamespace(ilike=lambda *_: None)):
        resp = client.get(
            "/admin/monitoreo/candidatas?q=Ana Search&limit=20",
            headers=_async_headers(),
            follow_redirects=False,
        )

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("success") is True
    assert payload.get("update_target") == "#monitoreoCandidatasAsyncRegion"
    assert "/admin/monitoreo/candidatas" in (payload.get("redirect_url") or "")
    html = payload.get("replace_html") or ""
    assert "Ana Search" in html
    assert "Ver historial" in html


def test_monitoreo_candidatas_search_fallback_clasico_intacto():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    resp = client.get("/admin/monitoreo/candidatas", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="monitoreoCandidatasAsyncRegion"' in html
    assert 'data-admin-async-form' in html


def test_resolver_alerta_async_desde_dashboard_refresca_region_de_alertas():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    rows = [
        {
            "id": 10,
            "severity": "critical",
            "action_type": "ERROR_EVENT",
            "summary": "Timeout de proveedor externo",
            "route": "/admin/monitoreo",
            "entity_type": "candidata",
            "entity_id": "901",
            "is_resolved": False,
        }
    ]
    with patch("admin.routes.resolve_alert", return_value=None), \
         patch("admin.routes.get_alert_items", return_value=rows):
        resp = client.post(
            "/admin/alertas/10/resolver",
            data={
                "next": "/admin/monitoreo",
                "_async_target": "#monitoreoAlertsAsyncRegion",
            },
            headers=_async_headers(),
            follow_redirects=False,
        )

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("success") is True
    assert payload.get("update_target") == "#monitoreoAlertsAsyncRegion"
    update_targets = payload.get("update_targets") or []
    assert isinstance(update_targets, list)
    assert len(update_targets) == 2
    assert update_targets[0].get("target") == "#monitoreoAlertsAsyncRegion"
    assert isinstance(update_targets[0].get("replace_html"), str)
    assert "Alertas críticas" in (update_targets[0].get("replace_html") or "")
    assert update_targets[1].get("target") == "#monitoreoDashboardShellAsyncRegion"
    assert update_targets[1].get("invalidate") is True
    assert payload.get("redirect_url") == "/admin/monitoreo"
    html = payload.get("replace_html") or ""
    assert "Timeout de proveedor externo" in html
    assert 'data-async-target="#monitoreoAlertsAsyncRegion"' in html


def test_resolver_alerta_async_desde_dashboard_error_no_invalida_shell():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    rows = [
        {
            "id": 10,
            "severity": "critical",
            "action_type": "ERROR_EVENT",
            "summary": "Timeout de proveedor externo",
            "route": "/admin/monitoreo",
            "entity_type": "candidata",
            "entity_id": "901",
            "is_resolved": False,
        }
    ]
    with patch("admin.routes.resolve_alert", side_effect=RuntimeError("boom")), \
         patch("admin.routes.get_alert_items", return_value=rows):
        resp = client.post(
            "/admin/alertas/10/resolver",
            data={
                "next": "/admin/monitoreo",
                "_async_target": "#monitoreoAlertsAsyncRegion",
            },
            headers=_async_headers(),
            follow_redirects=False,
        )

    assert resp.status_code == 500
    payload = resp.get_json() or {}
    assert payload.get("success") is False
    assert payload.get("update_target") == "#monitoreoAlertsAsyncRegion"
    update_targets = payload.get("update_targets") or []
    assert isinstance(update_targets, list)
    assert len(update_targets) == 1
    assert update_targets[0].get("target") == "#monitoreoAlertsAsyncRegion"
    assert update_targets[0].get("replace_html")
    assert payload.get("error_code") == "server_error"


def test_errores_detalle_respeta_next_seguro():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    with flask_app.app_context():
        actor = StaffUser.query.filter_by(username="Cruz").first()
        assert actor is not None
        row = StaffAuditLog(
            created_at=datetime.utcnow(),
            actor_user_id=actor.id,
            actor_role=actor.role,
            action_type="ERROR_EVENT",
            entity_type="system",
            entity_id="0",
            summary="Fallo de prueba",
            route="/admin/monitoreo",
            error_message="trace",
            metadata_json={"error_type": "SERVER_ERROR"},
            success=False,
        )
        db.session.add(row)
        db.session.commit()
        error_id = int(row.id)

    resp_safe = client.get(f"/admin/errores/{error_id}?next=/admin/monitoreo", follow_redirects=False)
    assert resp_safe.status_code == 200
    html_safe = resp_safe.get_data(as_text=True)
    assert 'href="/admin/monitoreo"' in html_safe

    resp_unsafe = client.get(f"/admin/errores/{error_id}?next=https://evil.example", follow_redirects=False)
    assert resp_unsafe.status_code == 200
    html_unsafe = resp_unsafe.get_data(as_text=True)
    assert 'href="/admin/errores"' in html_unsafe
