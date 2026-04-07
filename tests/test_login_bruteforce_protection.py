# -*- coding: utf-8 -*-

import re

from app import app as flask_app
from config_app import db
from models import Cliente
from werkzeug.security import generate_password_hash


_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _extract_csrf(html: str) -> str:
    m = _CSRF_RE.search(html or "")
    return m.group(1) if m else ""


def _admin_post_login(client, usuario: str, clave: str, ip: str):
    page = client.get("/admin/login", follow_redirects=False, environ_overrides={"REMOTE_ADDR": ip})
    assert page.status_code == 200
    token = _extract_csrf(page.data.decode("utf-8", errors="ignore"))
    assert token
    return client.post(
        "/admin/login",
        data={"usuario": usuario, "clave": clave, "csrf_token": token},
        follow_redirects=False,
        environ_overrides={"REMOTE_ADDR": ip},
    )


def _cliente_post_login(client, username: str, password: str, ip: str):
    page = client.get("/clientes/login", follow_redirects=False, environ_overrides={"REMOTE_ADDR": ip})
    assert page.status_code == 200
    token = _extract_csrf(page.data.decode("utf-8", errors="ignore"))
    assert token
    return client.post(
        "/clientes/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
        environ_overrides={"REMOTE_ADDR": ip},
    )


def _ensure_test_cliente() -> str:
    username = "cliente_seguridad_test"
    with flask_app.app_context():
        row = Cliente.query.filter(Cliente.username == username).first()
        if row is None:
            row = Cliente(
                codigo="CLSEG001",
                nombre_completo="Cliente Seguridad Test",
                email="cliente.seguridad.test@app.local",
                telefono="8090000000",
                username=username,
                is_active=True,
            )
            row.password_hash = generate_password_hash("Segura#12345", method="pbkdf2:sha256")
            db.session.add(row)
            db.session.commit()
    return username


def test_login_legitimo_funciona_con_rate_limit_activo(monkeypatch):
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = True

    monkeypatch.setenv("ENABLE_OPERATIONAL_RATE_LIMITS", "1")

    try:
        client = flask_app.test_client()
        admin_ok = _admin_post_login(client, "Cruz", "8998", "10.90.0.10")
        assert admin_ok.status_code in (302, 303)

        username = _ensure_test_cliente()
        cliente_ok = _cliente_post_login(client, username, "Segura#12345", "10.90.0.11")
        assert cliente_ok.status_code in (302, 303)
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf


def test_rate_limit_login_5_por_minuto_aplica_admin_y_cliente(monkeypatch):
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = True

    monkeypatch.setenv("ENABLE_OPERATIONAL_RATE_LIMITS", "1")
    monkeypatch.setenv("LOGIN_RATE_IP_1M", "5")
    monkeypatch.setenv("LOGIN_RATE_USER_1M", "5")
    monkeypatch.setenv("LOGIN_RATE_IP_1H", "200")
    monkeypatch.setenv("LOGIN_RATE_USER_1H", "200")
    monkeypatch.setenv("LOGIN_BLOCK_THRESHOLD", "50")
    monkeypatch.setenv("ADMIN_LOGIN_MAX_INTENTOS", "50")
    monkeypatch.setenv("CLIENTE_LOGIN_MAX_INTENTOS", "50")
    monkeypatch.setenv("LOGIN_DELAY_MS_BASE", "1")

    try:
        c_admin = flask_app.test_client()
        statuses_admin = []
        for _ in range(6):
            resp = _admin_post_login(c_admin, "Cruz", "clave-incorrecta", "10.90.0.20")
            statuses_admin.append(resp.status_code)
        assert statuses_admin[-1] == 429

        username = _ensure_test_cliente()
        c_cliente = flask_app.test_client()
        statuses_cliente = []
        for _ in range(6):
            resp = _cliente_post_login(c_cliente, username, "bad-password", "10.90.0.21")
            statuses_cliente.append(resp.status_code)
        assert statuses_cliente[-1] == 429
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf


def test_bloqueo_por_fallos_usuario_aplica_a_ips_distintas(monkeypatch):
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = True

    monkeypatch.setenv("ENABLE_OPERATIONAL_RATE_LIMITS", "1")
    monkeypatch.setenv("LOGIN_RATE_IP_1M", "200")
    monkeypatch.setenv("LOGIN_RATE_USER_1M", "200")
    monkeypatch.setenv("LOGIN_RATE_IP_1H", "200")
    monkeypatch.setenv("LOGIN_RATE_USER_1H", "200")
    monkeypatch.setenv("LOGIN_BLOCK_THRESHOLD", "10")
    monkeypatch.setenv("ADMIN_LOGIN_MAX_INTENTOS", "100")
    monkeypatch.setenv("LOGIN_DELAY_MS_BASE", "1")

    try:
        client = flask_app.test_client()
        username = "lock_user_cross_ip_test"
        for idx in range(9):
            ip = f"10.90.1.{idx + 1}"
            resp = _admin_post_login(client, username, "clave-incorrecta", ip)
            assert resp.status_code != 429

        tenth = _admin_post_login(client, username, "clave-incorrecta", "10.90.1.200")
        assert tenth.status_code == 429

        blocked_other_ip = _admin_post_login(client, username, "clave-incorrecta", "10.90.1.201")
        assert blocked_other_ip.status_code == 429
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf


def test_post_login_sin_usuario_no_activa_bloqueo_normal_por_ip(monkeypatch):
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = True

    monkeypatch.setenv("ENABLE_OPERATIONAL_RATE_LIMITS", "1")
    monkeypatch.setenv("LOGIN_RATE_IP_1M", "500")
    monkeypatch.setenv("LOGIN_RATE_USER_1M", "500")
    monkeypatch.setenv("LOGIN_RATE_IP_1H", "500")
    monkeypatch.setenv("LOGIN_RATE_USER_1H", "500")
    monkeypatch.setenv("LOGIN_BLOCK_THRESHOLD", "3")
    monkeypatch.setenv("LOGIN_BLOCK_THRESHOLD_IP", "50")
    monkeypatch.setenv("ADMIN_LOGIN_MAX_INTENTOS", "100")
    monkeypatch.setenv("LOGIN_DELAY_MS_BASE", "1")

    try:
        client = flask_app.test_client()
        statuses = []
        for _ in range(6):
            resp = _admin_post_login(client, "", "clave-incorrecta", "10.90.3.10")
            statuses.append(resp.status_code)
        assert all(code != 429 for code in statuses)
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf


def test_error_de_login_es_generico_sin_enumerar_usuario(monkeypatch):
    prev_testing = bool(flask_app.config.get("TESTING"))
    prev_csrf = bool(flask_app.config.get("WTF_CSRF_ENABLED", True))
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = True

    monkeypatch.setenv("ENABLE_OPERATIONAL_RATE_LIMITS", "1")
    monkeypatch.setenv("LOGIN_RATE_IP_1M", "200")
    monkeypatch.setenv("LOGIN_RATE_USER_1M", "200")
    monkeypatch.setenv("LOGIN_RATE_IP_1H", "200")
    monkeypatch.setenv("LOGIN_RATE_USER_1H", "200")
    monkeypatch.setenv("LOGIN_BLOCK_THRESHOLD", "50")
    monkeypatch.setenv("ADMIN_LOGIN_MAX_INTENTOS", "50")
    monkeypatch.setenv("CLIENTE_LOGIN_MAX_INTENTOS", "50")
    monkeypatch.setenv("LOGIN_DELAY_MS_BASE", "1")

    try:
        c_admin = flask_app.test_client()
        admin_resp = _admin_post_login(c_admin, "usuario_inexistente", "x", "10.90.2.10")
        assert admin_resp.status_code == 200
        assert "Credenciales incorrectas" in admin_resp.data.decode("utf-8", errors="ignore")

        c_cliente = flask_app.test_client()
        page = c_cliente.get("/clientes/login", follow_redirects=False, environ_overrides={"REMOTE_ADDR": "10.90.2.11"})
        token = _extract_csrf(page.data.decode("utf-8", errors="ignore"))
        assert token
        follow = c_cliente.post(
            "/clientes/login",
            data={"username": "no_existe_cliente", "password": "xxxxxx", "csrf_token": token},
            follow_redirects=True,
            environ_overrides={"REMOTE_ADDR": "10.90.2.11"},
        )
        assert follow.status_code == 200
        body = follow.data.decode("utf-8", errors="ignore")
        assert "Credenciales incorrectas" in body
    finally:
        flask_app.config["TESTING"] = prev_testing
        flask_app.config["WTF_CSRF_ENABLED"] = prev_csrf
