# -*- coding: utf-8 -*-

import re
import pytest

from app import app as flask_app


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _mark_cliente_session(client):
    with client.session_transaction() as sess:
        sess["usuario"] = "cliente_demo"
        sess["role"] = "cliente"
        sess["is_admin_session"] = True
        sess["mfa_verified"] = True
        sess["logged_at"] = "2026-05-05T10:00:00"


def _pick_solicitud_id_for(client):
    resp = client.get("/admin/solicitudes", follow_redirects=False)
    if resp.status_code != 200:
        return None
    html = resp.get_data(as_text=True)
    m = re.search(r'id=\"sol-(\\d+)\"', html)
    if not m:
        return None
    return int(m.group(1))


def test_admin_quick_view_fragment_staff_access_and_denied_for_others():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    for usuario, clave in (("Owner", "admin123"), ("Cruz", "8998"), ("Karla", "9989")):
        client = flask_app.test_client()
        assert _login(client, usuario, clave).status_code in (302, 303)
        sid = _pick_solicitud_id_for(client)
        if sid is None:
            pytest.skip("No hay solicitudes en el fixture para validar quick-view.")
        ok = client.get(f"/admin/solicitudes/{sid}/quick-view", follow_redirects=False)
        assert ok.status_code == 200

    anon = flask_app.test_client()
    denied_anon = anon.get("/admin/solicitudes/10/quick-view", follow_redirects=False)
    assert denied_anon.status_code in (302, 303)

    cliente_client = flask_app.test_client()
    _mark_cliente_session(cliente_client)
    denied_cliente = cliente_client.get("/admin/solicitudes/10/quick-view", follow_redirects=False)
    assert denied_cliente.status_code in (302, 303, 403)


def test_secretarias_texto_endpoint_staff_access_and_denied_for_others():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    for usuario, clave in (("Owner", "admin123"), ("Cruz", "8998"), ("Karla", "9989")):
        client = flask_app.test_client()
        assert _login(client, usuario, clave).status_code in (302, 303)
        sid = _pick_solicitud_id_for(client)
        if sid is None:
            pytest.skip("No hay solicitudes en el fixture para validar /texto.")
        ok = client.get(f"/secretarias/solicitudes/{sid}/texto", follow_redirects=False)
        assert ok.status_code == 200
        payload = ok.get_json(silent=True) or {}
        assert payload.get("ok") is True
        assert int(payload.get("id") or 0) == sid
        assert isinstance(payload.get("order_text"), str)
        assert payload.get("order_text")

    anon = flask_app.test_client()
    denied_anon = anon.get("/secretarias/solicitudes/1/texto", follow_redirects=False)
    assert denied_anon.status_code in (302, 303)

    cliente_client = flask_app.test_client()
    _mark_cliente_session(cliente_client)
    denied_cliente = cliente_client.get("/secretarias/solicitudes/1/texto", follow_redirects=False)
    assert denied_cliente.status_code in (302, 303, 403)


def test_admin_copiar_texto_endpoint_admin_owner_only():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    for usuario, clave in (("Owner", "admin123"), ("Cruz", "8998")):
        client = flask_app.test_client()
        assert _login(client, usuario, clave).status_code in (302, 303)
        sid = _pick_solicitud_id_for(client)
        if sid is None:
            pytest.skip("No hay solicitudes en el fixture para validar /admin/solicitudes/<id>/texto.")
        ok = client.get(f"/admin/solicitudes/{sid}/texto", follow_redirects=False)
        assert ok.status_code == 200
        payload = ok.get_json(silent=True) or {}
        assert payload.get("ok") is True
        assert int(payload.get("id") or 0) == sid
        assert isinstance(payload.get("order_text"), str)
        assert payload.get("order_text")

    sec_client = flask_app.test_client()
    assert _login(sec_client, "Karla", "9989").status_code in (302, 303)
    denied_sec = sec_client.get("/admin/solicitudes/1/texto", follow_redirects=False)
    assert denied_sec.status_code in (302, 303, 403)
