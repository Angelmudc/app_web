# -*- coding: utf-8 -*-
import hashlib

from app import app as flask_app
from config_app import db
from models import StaffUser, TrustedDevice
from utils.staff_mfa import MFA_SETUP_SECRET_SESSION_KEY, generate_totp_token, mfa_enforced_for_staff


def _login_admin(client, usuario: str, clave: str, **kwargs):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False, **kwargs)


def _set_staff_mfa(*, username: str, enabled: bool, secret: str = ""):
    with flask_app.app_context():
        user = StaffUser.query.filter_by(username=username).first()
        assert user is not None
        user.mfa_enabled = bool(enabled)
        user.mfa_last_timestep = None
        if enabled:
            user.set_mfa_secret(secret)
        else:
            user.mfa_secret = None
        db.session.commit()


def _trusted_devices_for(username: str) -> list[TrustedDevice]:
    with flask_app.app_context():
        user = StaffUser.query.filter_by(username=username).first()
        assert user is not None
        return list(TrustedDevice.query.filter_by(user_id=int(user.id)).all())


def _flip_code(valid_code: str) -> str:
    if not valid_code:
        return "000000"
    first = "0" if valid_code[0] != "0" else "1"
    return first + valid_code[1:]


def _sha256(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _cookie_value_from_response(resp, cookie_name: str) -> str:
    prefix = f"{cookie_name}="
    for raw in resp.headers.getlist("Set-Cookie"):
        txt = str(raw or "")
        if txt.startswith(prefix):
            return txt[len(prefix):].split(";", 1)[0]
    return ""


def _legacy_fingerprint(user_id: int, token: str, user_agent: str, ip_addr: str) -> str:
    octets = (ip_addr or "").split(".")
    if len(octets) == 4:
        ip_bucket = ".".join(octets[:3] + ["0"])
    else:
        ip_bucket = (ip_addr or "").strip()
    payload = f"v1|{int(user_id)}|{token}|{(user_agent or '').lower()}|{ip_bucket}"
    return _sha256(payload)


def _enable_mfa_flags(monkeypatch):
    monkeypatch.setenv("STAFF_MFA_REQUIRED", "1")
    monkeypatch.setenv("STAFF_MFA_ENFORCE_IN_TESTS", "1")


def test_staff_login_with_mfa_enabled_is_blocked_until_totp(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _enable_mfa_flags(monkeypatch)

    secret = "JBSWY3DPEHPK3PXP"
    _set_staff_mfa(username="Cruz", enabled=True, secret=secret)

    client = flask_app.test_client()
    resp = _login_admin(client, "Cruz", "8998")
    assert resp.status_code in (302, 303)
    assert "/admin/mfa/verify" in (resp.headers.get("Location") or "")

    home = client.get("/home", follow_redirects=False)
    assert home.status_code in (302, 303)
    assert "/admin/mfa/verify" in (home.headers.get("Location") or "")


def test_staff_login_with_valid_totp_allows_access(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _enable_mfa_flags(monkeypatch)

    secret = "JBSWY3DPEHPK3PXP"
    _set_staff_mfa(username="Cruz", enabled=True, secret=secret)

    client = flask_app.test_client()
    start = _login_admin(client, "Cruz", "8998")
    assert start.status_code in (302, 303)
    assert "/admin/mfa/verify" in (start.headers.get("Location") or "")

    code = generate_totp_token(secret)
    verify = client.post("/admin/mfa/verify", data={"code": code}, follow_redirects=False)
    assert verify.status_code in (302, 303)

    home = client.get("/home", follow_redirects=False)
    assert home.status_code == 200


def test_trusted_device_skips_2fa_after_first_success(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _enable_mfa_flags(monkeypatch)

    secret = "JBSWY3DPEHPK3PXP"
    _set_staff_mfa(username="Cruz", enabled=True, secret=secret)

    client = flask_app.test_client()

    first = _login_admin(client, "Cruz", "8998", environ_overrides={"REMOTE_ADDR": "10.10.8.12"})
    assert first.status_code in (302, 303)
    assert "/admin/mfa/verify" in (first.headers.get("Location") or "")

    code = generate_totp_token(secret)
    verify = client.post(
        "/admin/mfa/verify",
        data={"code": code},
        follow_redirects=False,
        environ_overrides={"REMOTE_ADDR": "10.10.8.12"},
    )
    assert verify.status_code in (302, 303)

    logout = client.post("/admin/logout", follow_redirects=False)
    assert logout.status_code in (302, 303)

    # Cambio de IP no crítico (misma /24) no debe romper el trusted lookup.
    second = _login_admin(client, "Cruz", "8998", environ_overrides={"REMOTE_ADDR": "10.10.8.99"})
    assert second.status_code in (302, 303)
    assert "/admin/mfa/" not in (second.headers.get("Location") or "")


def test_new_client_device_requires_2fa(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _enable_mfa_flags(monkeypatch)

    secret = "JBSWY3DPEHPK3PXP"
    _set_staff_mfa(username="Cruz", enabled=True, secret=secret)

    c1 = flask_app.test_client()
    start = _login_admin(c1, "Cruz", "8998")
    assert start.status_code in (302, 303)
    assert "/admin/mfa/verify" in (start.headers.get("Location") or "")
    done = c1.post("/admin/mfa/verify", data={"code": generate_totp_token(secret)}, follow_redirects=False)
    assert done.status_code in (302, 303)

    c2 = flask_app.test_client()
    second_device = _login_admin(c2, "Cruz", "8998")
    assert second_device.status_code in (302, 303)
    assert "/admin/mfa/verify" in (second_device.headers.get("Location") or "")


def test_trusted_lookup_uses_user_id_plus_device_token_hash(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _enable_mfa_flags(monkeypatch)

    secret = "JBSWY3DPEHPK3PXP"
    _set_staff_mfa(username="Cruz", enabled=True, secret=secret)
    _set_staff_mfa(username="Karla", enabled=True, secret=secret)

    client = flask_app.test_client()
    start = _login_admin(client, "Cruz", "8998")
    assert start.status_code in (302, 303)
    assert "/admin/mfa/verify" in (start.headers.get("Location") or "")

    done = client.post("/admin/mfa/verify", data={"code": generate_totp_token(secret)}, follow_redirects=False)
    assert done.status_code in (302, 303)

    # Mismo token cookie pero usuario diferente => NO debe confiar.
    logout = client.post("/admin/logout", follow_redirects=False)
    assert logout.status_code in (302, 303)
    other_user = _login_admin(client, "Karla", "9989")
    assert other_user.status_code in (302, 303)
    assert "/admin/mfa/verify" in (other_user.headers.get("Location") or "")


def test_mfa_success_registers_trusted_device(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _enable_mfa_flags(monkeypatch)

    secret = "JBSWY3DPEHPK3PXP"
    _set_staff_mfa(username="Cruz", enabled=True, secret=secret)

    client = flask_app.test_client()
    before = _trusted_devices_for("Cruz")
    start = _login_admin(client, "Cruz", "8998")
    assert start.status_code in (302, 303)
    assert "/admin/mfa/verify" in (start.headers.get("Location") or "")

    done = client.post("/admin/mfa/verify", data={"code": generate_totp_token(secret)}, follow_redirects=False)
    assert done.status_code in (302, 303)
    after = _trusted_devices_for("Cruz")
    assert len(after) >= (len(before) + 1)
    assert any(bool(d.is_trusted) and bool((d.device_token_hash or "").strip()) for d in after)


def test_backfill_legacy_device_token_hash_keeps_trust(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _enable_mfa_flags(monkeypatch)

    secret = "JBSWY3DPEHPK3PXP"
    _set_staff_mfa(username="Cruz", enabled=True, secret=secret)
    user_agent = "pytest-agent/1.0"
    ip_addr = "10.30.7.10"

    client = flask_app.test_client()
    start = _login_admin(
        client,
        "Cruz",
        "8998",
        headers={"User-Agent": user_agent},
        environ_overrides={"REMOTE_ADDR": ip_addr},
    )
    assert start.status_code in (302, 303)
    assert "/admin/mfa/verify" in (start.headers.get("Location") or "")

    done = client.post(
        "/admin/mfa/verify",
        data={"code": generate_totp_token(secret)},
        follow_redirects=False,
        headers={"User-Agent": user_agent},
        environ_overrides={"REMOTE_ADDR": ip_addr},
    )
    assert done.status_code in (302, 303)
    token = _cookie_value_from_response(done, "trusted_device_token")
    assert token
    expected_hash = _sha256(token)

    with flask_app.app_context():
        user = StaffUser.query.filter_by(username="Cruz").first()
        assert user is not None
        td = TrustedDevice.query.filter_by(user_id=int(user.id), device_token_hash=expected_hash).first()
        assert td is not None
        legacy_fp = _legacy_fingerprint(int(user.id), token, user_agent, ip_addr)
        td.device_fingerprint = legacy_fp
        td.device_token_hash = legacy_fp  # Simula estado post-backfill legacy.
        db.session.commit()

    logout = client.post("/admin/logout", follow_redirects=False)
    assert logout.status_code in (302, 303)

    # Debe seguir entrando sin 2FA y migrar hash al formato nuevo en caliente.
    second = _login_admin(
        client,
        "Cruz",
        "8998",
        headers={"User-Agent": user_agent},
        environ_overrides={"REMOTE_ADDR": ip_addr},
    )
    assert second.status_code in (302, 303)
    assert "/admin/mfa/" not in (second.headers.get("Location") or "")

    with flask_app.app_context():
        user = StaffUser.query.filter_by(username="Cruz").first()
        assert user is not None
        migrated = TrustedDevice.query.filter_by(user_id=int(user.id), device_token_hash=expected_hash).first()
        assert migrated is not None


def test_staff_login_with_invalid_totp_is_denied(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _enable_mfa_flags(monkeypatch)

    secret = "JBSWY3DPEHPK3PXP"
    _set_staff_mfa(username="Cruz", enabled=True, secret=secret)

    client = flask_app.test_client()
    start = _login_admin(client, "Cruz", "8998")
    assert start.status_code in (302, 303)
    assert "/admin/mfa/verify" in (start.headers.get("Location") or "")

    bad = _flip_code(generate_totp_token(secret))
    verify = client.post("/admin/mfa/verify", data={"code": bad}, follow_redirects=False)
    assert verify.status_code == 200
    body = verify.data.decode("utf-8", errors="ignore")
    assert "Código de verificación inválido" in body

    home = client.get("/home", follow_redirects=False)
    assert home.status_code in (302, 303)
    assert "/admin/mfa/verify" in (home.headers.get("Location") or "")


def test_staff_without_mfa_is_forced_to_setup(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _enable_mfa_flags(monkeypatch)

    _set_staff_mfa(username="Cruz", enabled=False)

    client = flask_app.test_client()
    start = _login_admin(client, "Cruz", "8998")
    assert start.status_code in (302, 303)
    assert "/admin/mfa/setup" in (start.headers.get("Location") or "")

    with client.session_transaction() as sess:
        setup_secret = str(sess.get(MFA_SETUP_SECRET_SESSION_KEY) or "")
    assert setup_secret

    code = generate_totp_token(setup_secret)
    done = client.post("/admin/mfa/setup", data={"code": code}, follow_redirects=False)
    assert done.status_code in (302, 303)

    home = client.get("/home", follow_redirects=False)
    assert home.status_code == 200


def test_cannot_bypass_mfa_by_session_tampering(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _enable_mfa_flags(monkeypatch)

    secret = "JBSWY3DPEHPK3PXP"
    _set_staff_mfa(username="Cruz", enabled=True, secret=secret)

    client = flask_app.test_client()
    start = _login_admin(client, "Cruz", "8998")
    assert start.status_code in (302, 303)
    assert "/admin/mfa/verify" in (start.headers.get("Location") or "")

    with client.session_transaction() as sess:
        sess["mfa_verified"] = True
        sess["is_admin_session"] = True
        sess["role"] = "admin"
        sess["usuario"] = "Cruz"

    home = client.get("/home", follow_redirects=False)
    assert home.status_code in (302, 303)
    assert "/admin/mfa/verify" in (home.headers.get("Location") or "")


def test_pending_mfa_blocks_admin_routes_and_json(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _enable_mfa_flags(monkeypatch)

    secret = "JBSWY3DPEHPK3PXP"
    _set_staff_mfa(username="Cruz", enabled=True, secret=secret)

    client = flask_app.test_client()
    start = _login_admin(client, "Cruz", "8998")
    assert start.status_code in (302, 303)
    assert "/admin/mfa/verify" in (start.headers.get("Location") or "")

    admin_html = client.get("/admin/clientes", follow_redirects=False)
    assert admin_html.status_code in (302, 303)
    loc_html = admin_html.headers.get("Location") or ""
    assert ("/admin/mfa/verify" in loc_html) or ("/admin/login" in loc_html)

    admin_json = client.get("/admin/monitoreo/summary.json", follow_redirects=False)
    assert admin_json.status_code in (302, 303)
    loc_json = admin_json.headers.get("Location") or ""
    assert ("/admin/mfa/verify" in loc_json) or ("/admin/login" in loc_json)

    live_json = client.get("/admin/live/invalidation/poll?after_id=0&limit=5", follow_redirects=False)
    assert live_json.status_code in (302, 303)
    assert "/admin/login" in (live_json.headers.get("Location") or "")

    sensitive_post = client.post("/admin/solicitudes/1/activar", follow_redirects=False)
    assert sensitive_post.status_code in (302, 303)
    loc_post = sensitive_post.headers.get("Location") or ""
    assert ("/admin/mfa/verify" in loc_post) or ("/admin/login" in loc_post)


def test_presence_tracker_disabled_on_login_and_mfa_verify(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _enable_mfa_flags(monkeypatch)

    secret = "JBSWY3DPEHPK3PXP"
    _set_staff_mfa(username="Cruz", enabled=True, secret=secret)

    client = flask_app.test_client()
    login_page = client.get("/admin/login", follow_redirects=False)
    assert login_page.status_code == 200
    login_html = login_page.data.decode("utf-8", errors="ignore")
    assert 'data-live-presence-enabled="0"' in login_html
    assert "/admin/monitoreo/presence/ping" not in login_html
    assert "js/core/control_room_presence.js" not in login_html

    start = _login_admin(client, "Cruz", "8998")
    assert start.status_code in (302, 303)
    assert "/admin/mfa/verify" in (start.headers.get("Location") or "")

    verify_page = client.get("/admin/mfa/verify", follow_redirects=False)
    assert verify_page.status_code == 200
    verify_html = verify_page.data.decode("utf-8", errors="ignore")
    assert 'data-live-presence-enabled="0"' in verify_html
    assert "/admin/monitoreo/presence/ping" not in verify_html
    assert "js/core/control_room_presence.js" not in verify_html


def test_totp_code_reuse_is_denied(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _enable_mfa_flags(monkeypatch)

    fixed_ts = 1_800_000_000
    monkeypatch.setattr("utils.staff_mfa.time.time", lambda: fixed_ts)

    secret = "JBSWY3DPEHPK3PXP"
    _set_staff_mfa(username="Cruz", enabled=True, secret=secret)
    code = generate_totp_token(secret, now_ts=fixed_ts)

    client = flask_app.test_client()
    first = _login_admin(client, "Cruz", "8998")
    assert first.status_code in (302, 303)
    assert "/admin/mfa/verify" in (first.headers.get("Location") or "")

    ok_verify = client.post("/admin/mfa/verify", data={"code": code}, follow_redirects=False)
    assert ok_verify.status_code in (302, 303)

    logout = client.post("/admin/logout", follow_redirects=False)
    assert logout.status_code in (302, 303)

    client_new_device = flask_app.test_client()
    second = _login_admin(client_new_device, "Cruz", "8998")
    assert second.status_code in (302, 303)
    assert "/admin/mfa/verify" in (second.headers.get("Location") or "")

    reused = client_new_device.post("/admin/mfa/verify", data={"code": code}, follow_redirects=False)
    assert reused.status_code == 200
    body = reused.data.decode("utf-8", errors="ignore")
    assert "Código ya utilizado" in body


def test_mfa_cancel_clears_pending_state(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _enable_mfa_flags(monkeypatch)

    secret = "JBSWY3DPEHPK3PXP"
    _set_staff_mfa(username="Cruz", enabled=True, secret=secret)

    client = flask_app.test_client()
    start = _login_admin(client, "Cruz", "8998")
    assert start.status_code in (302, 303)
    assert "/admin/mfa/verify" in (start.headers.get("Location") or "")

    cancel = client.post("/admin/mfa/cancel", follow_redirects=False)
    assert cancel.status_code in (302, 303)
    assert "/admin/login" in (cancel.headers.get("Location") or "")

    with client.session_transaction() as sess:
        assert "_staff_mfa_pending" not in sess
        assert MFA_SETUP_SECRET_SESSION_KEY not in sess

    verify = client.get("/admin/mfa/verify", follow_redirects=False)
    assert verify.status_code in (302, 303)
    assert "/admin/login" in (verify.headers.get("Location") or "")


def test_mfa_flag_fail_closed_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("STAFF_MFA_REQUIRED", "0")
    monkeypatch.delenv("STAFF_MFA_ALLOW_DISABLE_IN_PROD", raising=False)
    assert mfa_enforced_for_staff(testing=False) is True
