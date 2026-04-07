# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from app import app as flask_app


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_html_csrf_expired_is_friendly_redirect_not_raw_400():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    client = flask_app.test_client()

    resp = client.post(
        "/login",
        data={"usuario": "Cruz", "clave": "8998"},
        follow_redirects=False,
    )

    assert resp.status_code in (302, 303)
    location = resp.headers.get("Location") or ""
    assert "/login" in location

    follow = client.get(location, follow_redirects=False)
    html = follow.get_data(as_text=True)
    assert "Bad Request" not in html
    assert "The CSRF token is missing." not in html


def test_async_csrf_expired_stays_json_400():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    client = flask_app.test_client()

    resp = client.post(
        "/login",
        data={"usuario": "Cruz", "clave": "8998"},
        headers={
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Admin-Async": "1",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 400
    payload = resp.get_json() or {}
    assert payload.get("error_code") == "csrf"
    assert payload.get("message") == "La sesión de seguridad expiró. Recarga la página e intenta de nuevo."
    errors = payload.get("errors") or []
    assert any((e or {}).get("field") == "csrf_token" for e in errors)


def test_post_without_csrf_still_blocked_does_not_authenticate():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    client = flask_app.test_client()

    resp = client.post(
        "/login",
        data={"usuario": "Cruz", "clave": "8998"},
        follow_redirects=False,
    )

    assert resp.status_code in (302, 303, 400)
    with client.session_transaction() as sess:
        assert not bool(sess.get("usuario"))
        assert not bool(sess.get("_user_id"))


def test_client_live_snapshot_restore_excludes_security_hidden_fields_and_keeps_regular_inputs():
    txt = _read("static/js/core/client_live_invalidation.js")
    assert 'if (fieldName === "csrf_token") return true;' in txt
    assert "if (fieldType !== \"hidden\") return false;" in txt
    assert "if (isSecurityField(el, name, type)) return;" in txt
    assert "values[key] = String(el.value || \"\");" in txt


def test_chat_and_async_use_dynamic_csrf_token_from_current_dom():
    admin_chat = _read("static/js/chat/admin_chat.js")
    client_chat = _read("static/js/chat/client_chat.js")
    admin_async = _read("static/js/core/admin_async.js")

    assert "function getCSRFToken()" in admin_chat
    assert "const csrfToken = getCSRFToken();" in admin_chat
    assert "const csrfToken = ((document.querySelector('meta[name=\"csrf-token\"]') || {}).content || \"\").trim();" not in admin_chat

    assert "function getCSRFToken()" in client_chat
    assert "const csrfToken = getCSRFToken();" in client_chat
    assert "const csrfToken = ((document.querySelector('meta[name=\"csrf-token\"]') || {}).content || \"\").trim();" not in client_chat

    assert "function getCSRFToken(form)" in admin_async
    assert "headers: { \"X-CSRFToken\": getCSRFToken(form) }" in admin_async
    assert "headers: { \"X-CSRFToken\": getCSRFToken(null) }" in admin_async
