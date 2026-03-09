# -*- coding: utf-8 -*-

from app import app as flask_app


def _login(client, usuario, clave):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _presence_row(summary_payload, username: str):
    rows = summary_payload.get("presence") or []
    for row in rows:
        if str(row.get("username") or "") == username:
            return row
    return {}


def test_presence_updates_target_on_same_route_and_clears_stale_entity():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    c_sec = flask_app.test_client()
    c_admin = flask_app.test_client()
    assert _login(c_sec, "Karla", "9989").status_code in (302, 303)
    assert _login(c_admin, "Cruz", "8998").status_code in (302, 303)

    first = c_sec.post(
        "/admin/monitoreo/presence/ping",
        json={
            "current_path": "/buscar",
            "event_type": "open_entity",
            "action_hint": "editing_candidate",
            "entity_type": "candidata",
            "entity_id": "582",
            "entity_name": "Maria Uno",
            "entity_code": "DOM-582",
        },
        follow_redirects=False,
    )
    assert first.status_code == 200

    switched = c_sec.post(
        "/admin/monitoreo/presence/ping",
        json={
            "current_path": "/buscar",
            "event_type": "open_entity",
            "action_hint": "editing_candidate",
            "entity_type": "candidata",
            "entity_id": "601",
            "entity_name": "Maria Dos",
            "entity_code": "DOM-601",
        },
        follow_redirects=False,
    )
    assert switched.status_code == 200

    summary = c_admin.get("/admin/monitoreo/summary.json", follow_redirects=False)
    assert summary.status_code == 200
    row = _presence_row(summary.get_json() or {}, "Karla")
    assert row.get("entity_type") == "candidata"
    assert row.get("entity_id") == "601"
    assert "DOM-601" in (row.get("entity_display") or "")
    assert "601" in (row.get("current_action_human") or "")

    clear_resp = c_sec.post(
        "/admin/monitoreo/presence/ping",
        json={
            "current_path": "/admin/solicitudes",
            "event_type": "page_load",
            "action_hint": "solicitudes",
        },
        follow_redirects=False,
    )
    assert clear_resp.status_code == 200

    summary_after_clear = c_admin.get("/admin/monitoreo/summary.json", follow_redirects=False)
    assert summary_after_clear.status_code == 200
    row_after_clear = _presence_row(summary_after_clear.get_json() or {}, "Karla")
    assert row_after_clear.get("entity_id") in ("", None)


def test_presence_extracts_target_from_solicitud_cliente_and_matching_routes():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    c_sec = flask_app.test_client()
    c_admin = flask_app.test_client()
    assert _login(c_sec, "Anyi", "0931").status_code in (302, 303)
    assert _login(c_admin, "Cruz", "8998").status_code in (302, 303)

    p1 = c_sec.post(
        "/admin/monitoreo/presence/ping",
        json={
            "current_path": "/admin/clientes/12/solicitudes/33/editar",
            "action_hint": "editing_request",
            "event_type": "page_load",
        },
        follow_redirects=False,
    )
    assert p1.status_code == 200

    s1 = c_admin.get("/admin/monitoreo/summary.json", follow_redirects=False)
    row1 = _presence_row(s1.get_json() or {}, "Anyi")
    assert row1.get("entity_type") == "solicitud"
    assert row1.get("entity_id") == "33"

    p2 = c_sec.post(
        "/admin/monitoreo/presence/ping",
        json={
            "current_path": "/admin/clientes/12",
            "action_hint": "viewing_client",
            "event_type": "page_load",
        },
        follow_redirects=False,
    )
    assert p2.status_code == 200

    s2 = c_admin.get("/admin/monitoreo/summary.json", follow_redirects=False)
    row2 = _presence_row(s2.get_json() or {}, "Anyi")
    assert row2.get("entity_type") == "cliente"
    assert row2.get("entity_id") == "12"
    assert "cliente" in (row2.get("current_action_human") or "").lower()

    p3 = c_sec.post(
        "/admin/monitoreo/presence/ping",
        json={
            "current_path": "/admin/matching/solicitudes/77",
            "action_hint": "matching",
            "event_type": "page_load",
        },
        follow_redirects=False,
    )
    assert p3.status_code == 200

    s3 = c_admin.get("/admin/monitoreo/summary.json", follow_redirects=False)
    row3 = _presence_row(s3.get_json() or {}, "Anyi")
    assert row3.get("entity_type") == "solicitud"
    assert row3.get("entity_id") == "77"
    assert "matching" in (row3.get("current_action_human") or "").lower()
