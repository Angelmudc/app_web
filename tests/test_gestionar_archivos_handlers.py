# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from flask import url_for

from app import app as flask_app


def _login(client, usuario: str = "Owner", clave: str = "admin123"):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def test_gestionar_archivos_endpoint_names_and_route_contract():
    with flask_app.app_context():
        with flask_app.test_request_context():
            assert url_for("gestionar_archivos") == "/gestionar_archivos"
            assert url_for("archivos_routes.gestionar_archivos") == "/gestionar_archivos"

    assert flask_app.view_functions["gestionar_archivos"].__module__ == "core.handlers.gestionar_archivos_handlers"


def test_gestionar_archivos_post_buscar_empty_redirects_to_buscar():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    resp = client.post("/gestionar_archivos?accion=buscar", data={"busqueda": "   "}, follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert (resp.headers.get("Location") or "").endswith("/gestionar_archivos?accion=buscar")


def test_gestionar_archivos_ver_preserves_document_flags_and_interview_text():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    cand = SimpleNamespace(
        depuracion=b"dep",
        perfil=b"",
        cedula1=None,
        cedula2=b"ced2",
        entrevista="  Entrevista lista  ",
    )

    class _Query:
        def filter_by(self, **_kwargs):
            return self

        def first(self):
            return cand

    captured = {}

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured["ctx"] = ctx
        return "ok"

    with patch("core.handlers.gestionar_archivos_handlers.Candidata", new=SimpleNamespace(query=_Query())), \
         patch("core.handlers.gestionar_archivos_handlers.render_template", side_effect=_fake_render):
        resp = client.get("/gestionar_archivos?accion=ver&fila=7", follow_redirects=False)

    assert resp.status_code == 200
    assert captured["template"] == "gestionar_archivos.html"
    assert captured["ctx"]["accion"] == "ver"
    assert captured["ctx"]["fila"] == 7
    assert captured["ctx"]["docs"] == {
        "depuracion": True,
        "perfil": False,
        "cedula1": False,
        "cedula2": True,
        "entrevista": "Entrevista lista",
    }


def test_gestionar_archivos_descargar_and_fallback_contract():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)

    pdf_resp = client.get("/gestionar_archivos?accion=descargar&doc=pdf&fila=5", follow_redirects=False)
    assert pdf_resp.status_code in (302, 303)
    assert (pdf_resp.headers.get("Location") or "").endswith("/generar_pdf_entrevista?fila=5")

    bad_fila = client.get("/gestionar_archivos?accion=descargar&doc=pdf&fila=x", follow_redirects=False)
    assert bad_fila.status_code == 400
    assert "Fila inválida" in bad_fila.get_data(as_text=True)

    bad_doc = client.get("/gestionar_archivos?accion=descargar&doc=otro&fila=5", follow_redirects=False)
    assert bad_doc.status_code == 400
    assert "Documento no reconocido" in bad_doc.get_data(as_text=True)

    fallback = client.get("/gestionar_archivos?accion=rara", follow_redirects=False)
    assert fallback.status_code in (302, 303)
    assert (fallback.headers.get("Location") or "").endswith("/gestionar_archivos?accion=buscar")
