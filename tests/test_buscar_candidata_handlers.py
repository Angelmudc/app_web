# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


def _build_candidata_stub(fila: int = 77):
    return SimpleNamespace(
        fila=fila,
        nombre_completo="Demo",
        edad="30",
        numero_telefono="8091111111",
        direccion_completa="Santiago",
        modalidad_trabajo_preferida="salida diaria",
        rutas_cercanas="Centro",
        empleo_anterior="Casa A",
        anos_experiencia="2",
        areas_experiencia="limpieza",
        contactos_referencias_laborales="Ref laboral vieja",
        referencias_familiares_detalle="Ref familiar vieja",
        referencias_laboral="Ref laboral vieja",
        referencias_familiares="Ref familiar vieja",
        cedula="001-0000000-1",
        sabe_planchar=True,
        acepta_porcentaje_sueldo=True,
        disponibilidad_inicio=None,
        trabaja_con_ninos=None,
        trabaja_con_mascotas=None,
        puede_dormir_fuera=None,
        sueldo_esperado=None,
        motivacion_trabajo=None,
    )


def test_buscar_candidata_endpoint_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("buscar_candidata") == "/buscar"
            assert url_for("candidatas_routes.buscar_candidata") == "/buscar"

    assert flask_app.view_functions["buscar_candidata"].__module__ == "core.handlers.buscar_candidata_handlers"


def test_buscar_candidata_get_search_render_contract():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    c1 = _build_candidata_stub(fila=10)
    c2 = _build_candidata_stub(fila=20)
    captured = {}

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.buscar_candidata_handlers.search_candidatas_limited", return_value=[c1, c2]), \
         patch("core.handlers.buscar_candidata_handlers._prioritize_candidata_result", return_value=[c2, c1]), \
         patch("core.handlers.buscar_candidata_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/buscar?busqueda=demo", follow_redirects=False)

    assert resp.status_code == 200
    assert captured["template"] == "buscar.html"
    assert captured["ctx"]["busqueda"] == "demo"
    assert captured["ctx"]["resultados"] == [c2, c1]
    assert captured["ctx"]["candidata"] is None


def test_buscar_candidata_get_candidata_id_loads_detail_and_sets_session():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=22)
    captured = {}

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.buscar_candidata_handlers.get_candidata_by_id", return_value=cand), \
         patch("core.handlers.buscar_candidata_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/buscar?candidata_id=22", follow_redirects=False)

    assert resp.status_code == 200
    assert captured["template"] == "buscar.html"
    assert captured["ctx"]["candidata"] is cand

    with client.session_transaction() as sess:
        assert sess.get("last_edited_candidata_fila") == 22


def test_buscar_candidata_post_edit_success_keeps_side_effects_and_redirect():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=88)
    ok_result = SimpleNamespace(ok=True, attempts=1, error_message="")

    with patch("core.handlers.buscar_candidata_handlers.get_candidata_by_id", return_value=cand), \
         patch("core.handlers.buscar_candidata_handlers.legacy_h.execute_robust_save", return_value=ok_result), \
         patch("core.handlers.buscar_candidata_handlers.legacy_h.snapshot_model_fields", side_effect=lambda _obj, _f: {}), \
         patch("core.handlers.buscar_candidata_handlers.legacy_h.diff_snapshots", return_value={}), \
         patch("core.handlers.buscar_candidata_handlers.legacy_h.log_candidata_action") as audit_mock:
        resp = client.post(
            "/buscar",
            data={
                "guardar_edicion": "1",
                "candidata_id": "88",
                "telefono": "8090001234",
                "contactos_referencias_laborales": "Laboral nueva",
                "referencias_familiares_detalle": "Familiar nueva",
            },
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert (resp.headers.get("Location") or "").endswith("/buscar?candidata_id=88")
    assert cand.numero_telefono == "8090001234"
    assert cand.referencias_laboral == "Laboral nueva"
    assert cand.referencias_familiares == "Familiar nueva"
    audit_mock.assert_called_once()

    with client.session_transaction() as sess:
        assert sess.get("last_edited_candidata_fila") == 88


def test_buscar_candidata_post_cedula_invalida_keeps_render_and_overrides():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=90)
    ok_result = SimpleNamespace(ok=True, attempts=1, error_message="")
    captured = {}

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.buscar_candidata_handlers.get_candidata_by_id", return_value=cand), \
         patch("core.handlers.buscar_candidata_handlers.legacy_h.normalize_cedula_for_compare", return_value=""), \
         patch("core.handlers.buscar_candidata_handlers.legacy_h.execute_robust_save", return_value=ok_result), \
         patch("core.handlers.buscar_candidata_handlers.legacy_h.snapshot_model_fields", side_effect=lambda _obj, _f: {}), \
         patch("core.handlers.buscar_candidata_handlers.legacy_h.diff_snapshots", return_value={}), \
         patch("core.handlers.buscar_candidata_handlers.render_template", side_effect=_fake_render):
        resp = client.post(
            "/buscar",
            data={
                "guardar_edicion": "1",
                "candidata_id": "90",
                "cedula": "---",
                "telefono": "8095551212",
            },
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert captured["template"] == "buscar.html"
    assert "Cédula inválida" in (captured["ctx"]["mensaje"] or "")
    assert captured["ctx"]["edit_form_overrides"]["cedula"] == "---"
    assert cand.numero_telefono == "8095551212"
    assert cand.cedula == "001-0000000-1"


def test_buscar_candidata_post_edit_success_con_next_redirige_al_contexto():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=91)
    ok_result = SimpleNamespace(ok=True, attempts=1, error_message="")

    with patch("core.handlers.buscar_candidata_handlers.get_candidata_by_id", return_value=cand), \
         patch("core.handlers.buscar_candidata_handlers.legacy_h.execute_robust_save", return_value=ok_result), \
         patch("core.handlers.buscar_candidata_handlers.legacy_h.snapshot_model_fields", side_effect=lambda _obj, _f: {}), \
         patch("core.handlers.buscar_candidata_handlers.legacy_h.diff_snapshots", return_value={}), \
         patch("core.handlers.buscar_candidata_handlers.legacy_h.log_candidata_action"):
        resp = client.post(
            "/buscar?next=/finalizar_proceso?fila=91",
            data={
                "guardar_edicion": "1",
                "candidata_id": "91",
                "telefono": "8099991111",
                "next": "/finalizar_proceso?fila=91",
            },
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert (resp.headers.get("Location") or "").endswith("/finalizar_proceso?fila=91")
