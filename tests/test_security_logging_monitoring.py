# -*- coding: utf-8 -*-

from __future__ import annotations

import time
import re
from unittest.mock import patch

import pytest

from app import app as flask_app
from config_app import db
from models import Cliente, StaffAuditLog
from utils.audit_logger import log_security_event
from werkzeug.security import generate_password_hash


def _login_admin(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _clear_audit_logs():
    db.session.query(StaffAuditLog).delete()
    db.session.commit()


def _extract_csrf(html: str) -> str:
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html or "")
    return (m.group(1).strip() if m else "")


def test_staff_login_logs_success_and_fail():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _clear_audit_logs()

    bad = _login_admin(client, "Cruz", "bad-pass")
    assert bad.status_code in (200, 302, 303)
    ok = _login_admin(client, "Cruz", "8998")
    assert ok.status_code in (302, 303)

    with flask_app.app_context():
        fail_row = (
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type == "STAFF_LOGIN_FAIL")
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        ok_row = (
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type == "STAFF_LOGIN_SUCCESS")
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        assert fail_row is not None
        assert ok_row is not None


def test_cliente_login_logs_success_and_fail():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    unique = f"sec_cli_{int(time.time() * 1000)}"
    with flask_app.app_context():
        _clear_audit_logs()
        cliente = Cliente(
            codigo=f"CLI-{unique[-6:]}",
            nombre_completo="Cliente Seguridad",
            email=f"{unique}@example.com",
            telefono="8090000000",
            username=unique,
            role="cliente",
            is_active=True,
        )
        cliente.password_hash = generate_password_hash("Segura#12345", method="pbkdf2:sha256")
        db.session.add(cliente)
        db.session.commit()

    page = client.get("/clientes/login", follow_redirects=False)
    token = _extract_csrf(page.data.decode("utf-8", errors="ignore"))
    bad = client.post(
        "/clientes/login",
        data={"username": unique, "password": "bad-pass-123", "csrf_token": token},
        follow_redirects=False,
    )
    assert bad.status_code in (302, 303)
    page_ok = client.get("/clientes/login", follow_redirects=False)
    token_ok = _extract_csrf(page_ok.data.decode("utf-8", errors="ignore"))
    ok = client.post(
        "/clientes/login",
        data={"username": unique, "password": "Segura#12345", "csrf_token": token_ok},
        follow_redirects=False,
    )
    assert ok.status_code in (302, 303)

    with flask_app.app_context():
        fail_row = (
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type == "CLIENTE_LOGIN_FAIL")
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        ok_row = (
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type == "CLIENTE_LOGIN_SUCCESS")
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        assert fail_row is not None
        assert ok_row is not None


def test_access_denied_is_logged():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _clear_audit_logs()

    assert _login_admin(client, "Karla", "9989").status_code in (302, 303)
    denied = client.get("/admin/roles", follow_redirects=False)
    assert denied.status_code == 403

    with flask_app.app_context():
        row = (
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type.in_(["PERMISSION_DENIED", "AUTHZ_DENIED"]))
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        assert row is not None


def test_sensitive_admin_action_logs_created():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _clear_audit_logs()

    assert _login_admin(client, "Owner", "admin123").status_code in (302, 303)
    unique = f"sec_staff_{int(time.time() * 1000)}"
    resp = client.post(
        "/admin/usuarios/nuevo",
        data={
            "username": unique,
            "email": f"{unique}@example.com",
            "role": "secretaria",
            "password": "ClaveSegura123",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    with flask_app.app_context():
        row = (
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type == "STAFF_USER_CREATE")
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        assert row is not None
        assert bool(row.success) is True


def test_repeated_access_denied_triggers_alert():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    ip_tail = int(time.time()) % 200 + 20
    fake_ip = f"10.77.88.{ip_tail}"

    with flask_app.app_context():
        _clear_audit_logs()
        with patch("utils.enterprise_layer._send_telegram_message", return_value=(True, "ok")):
            with flask_app.test_request_context(
                "/admin/roles",
                method="GET",
                environ_base={"REMOTE_ADDR": fake_ip},
            ):
                for _ in range(6):
                    log_security_event(
                        event="AUTHZ_DENIED",
                        status="fail",
                        reason="forbidden_test",
                        metadata={"path": "/admin/roles"},
                    )

        alert_row = (
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type.in_(["ALERT_WARNING", "ALERT_CRITICAL"]))
            .filter(StaffAuditLog.summary.ilike("%Accesos denegados%"))
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        assert alert_row is not None


def test_audit_logs_are_immutable_in_production_mode(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        monkeypatch.setenv("APP_ENV", "production")
        row = StaffAuditLog(action_type="IMMUTABLE_CHECK", success=True, metadata_json={"k": "v"})
        db.session.add(row)
        db.session.commit()

        db.session.delete(row)
        with pytest.raises(RuntimeError):
            db.session.commit()
        db.session.rollback()
