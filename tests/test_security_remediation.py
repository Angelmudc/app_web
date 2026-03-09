# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import render_template

from app import app as flask_app
from config_app import create_app
from models import StaffAuditLog
from utils.audit_logger import log_action


def _login_admin(client, usuario="Cruz", clave="8998"):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def test_client_reset_password_route_disabled_and_no_redirect_flow():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    resp = client.get("/clientes/reset-password", follow_redirects=False)
    assert resp.status_code == 410
    body = resp.data.decode("utf-8", errors="ignore").lower()
    assert "deshabilitada" in body or "no disponible" in body
    with open("clientes/routes.py", "r", encoding="utf-8") as fh:
        routes_source = fh.read()
    assert "url_for('clientes.reset_password'" not in routes_source


def test_domestica_detail_escapes_experiencia_detallada():
    flask_app.config["TESTING"] = True
    payload = "<script>alert('x')</script>\nlinea2"
    cand = SimpleNamespace(fila=1, codigo="DOM-1", nombre_completo="Ana Demo", foto_perfil=None)
    ficha = {
        "nombre_publico": "Ana",
        "edad_publica": "30",
        "frase_destacada": "",
        "tipo_servicio_publico": "",
        "disponible_inmediato": True,
        "disponible_inmediato_msg": "",
        "ciudad_publica": "",
        "sector_publico": "",
        "modalidad_publica": "",
        "anos_experiencia_publicos": "",
        "sueldo_texto_publico": "",
        "sueldo_desde": None,
        "sueldo_hasta": None,
        "experiencia_resumen": "",
        "experiencia_detallada": payload,
        "tags_publicos": "",
    }
    with flask_app.test_request_context("/clientes/domesticas/1"):
        html = render_template("clientes/domesticas_detail.html", cand=cand, ficha=ficha)
    assert payload not in html
    assert "&lt;script&gt;" in html


def test_monitoreo_live_uses_safe_dom_updates_for_presence_sections():
    js_path = "static/js/monitoreo_live.js"
    with open(js_path, "r", encoding="utf-8") as fh:
        source = fh.read()

    top_section = source.split("function updateTopList", 1)[1].split("function updatePresenceTable", 1)[0]
    presence_section = source.split("function updatePresenceTable", 1)[1].split("function updateOperations", 1)[0]
    conflicts_section = source.split("function updateConflicts", 1)[1].split("function updateActivityStream", 1)[0]

    assert "li.innerHTML" not in top_section
    assert "tr.innerHTML" not in presence_section
    assert "box.innerHTML = rows.map" not in conflicts_section
    assert "textContent" in top_section
    assert "textContent" in presence_section


def test_audit_logger_masks_sensitive_metadata_and_changes():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        with flask_app.test_request_context("/admin/monitoreo", method="POST"):
            log_action(
                action_type="SECURITY_TEST",
                summary="masking test",
                metadata={
                    "telefono": "8099999999",
                    "cedula": "001-0000000-1",
                    "direccion_completa": "Calle 123",
                    "email": "cliente@example.com",
                    "safe_field": "ok",
                },
                changes={
                    "numero_telefono": {"from": "8091111111", "to": "8092222222"},
                    "nombre_completo": {"from": "A", "to": "B"},
                },
            )

        row = (
            StaffAuditLog.query
            .filter_by(action_type="SECURITY_TEST")
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        assert row is not None
        meta = dict(row.metadata_json or {})
        assert meta.get("safe_field") == "ok"
        assert str(meta.get("telefono", "")).startswith("***")
        assert str(meta.get("cedula", "")).startswith("***")
        assert meta.get("direccion_completa") == "<redacted_address>"
        assert meta.get("email", "").startswith("c***@")

        changes = dict(row.changes_json or {})
        assert "numero_telefono" in changes
        assert changes["numero_telefono"]["from"] in ("<redacted>", "<hidden>") or str(changes["numero_telefono"]["from"]).startswith("***")
        assert changes["nombre_completo"]["to"] == "B"


def test_logout_requires_post_admin_and_legacy():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_admin(client).status_code in (302, 303)

    assert client.get("/admin/logout", follow_redirects=False).status_code == 405
    post_admin = client.post("/admin/logout", follow_redirects=False)
    assert post_admin.status_code in (302, 303)

    assert _login_admin(client).status_code in (302, 303)
    assert client.get("/logout", follow_redirects=False).status_code == 405
    post_legacy = client.post("/logout", follow_redirects=False)
    assert post_legacy.status_code in (302, 303)


def test_create_app_requires_secret_key_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    with pytest.raises(RuntimeError):
        create_app()


def test_create_app_sets_secure_cookies_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("FLASK_SECRET_KEY", "x" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("ALLOW_INSECURE_PROD_COOKIES", raising=False)

    app = create_app()
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["REMEMBER_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True


def test_autosave_uses_session_storage_and_sensitive_filters():
    with open("static/js/forms/autosave.js", "r", encoding="utf-8") as fh:
        autosave_source = fh.read()
    assert "sessionStorage" in autosave_source
    assert "localStorage.setItem(k" not in autosave_source
    assert "cedula" in autosave_source and "direccion" in autosave_source

    with open("static/clientes/js/clientes.js", "r", encoding="utf-8") as fh:
        clientes_source = fh.read()
    autosave_section = clientes_source.split("function initAutosave()", 1)[1].split("function initPrefetch()", 1)[0]
    assert "sessionStorage" in autosave_section
    assert "localStorage.setItem(key" not in autosave_section
    assert "SENSITIVE_TOKENS" in autosave_section
