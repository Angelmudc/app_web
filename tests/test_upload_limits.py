# -*- coding: utf-8 -*-

import io

from app import app as flask_app
from models import StaffAuditLog


class _DummyCandidata:
    def __init__(self):
        self.fila = 1
        self.id = 1
        self.codigo = "C-001"
        self.cedula = "001-0000000-1"
        self.nombre_completo = "Demo"
        self.estado = "inscrita"
        self.depuracion = b"dep-old"
        self.perfil = b"perfil-old"
        self.cedula1 = b"c1-old"
        self.cedula2 = b"c2-old"


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


def test_rejects_oversized_single_file_no_db_change():
    from unittest.mock import patch

    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["APP_MAX_FILE_BYTES"] = 16
    flask_app.config["APP_MAX_FILE_MB"] = 0.0

    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)
    cand = _DummyCandidata()
    before = cand.depuracion

    with flask_app.app_context():
        pre_count = StaffAuditLog.query.filter(StaffAuditLog.action_type == "CANDIDATA_UPLOAD_DOCS_SIZE_REJECT").count()

    with patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand):
        resp = client.post(
            "/subir_fotos?accion=subir&fila=1",
            data={"depuracion": (io.BytesIO(b"x" * 64), "depuracion.jpg")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert cand.depuracion == before

    with flask_app.app_context():
        post_count = StaffAuditLog.query.filter(StaffAuditLog.action_type == "CANDIDATA_UPLOAD_DOCS_SIZE_REJECT").count()
    assert post_count == pre_count + 1


def test_accepts_under_limit_and_saves():
    from unittest.mock import patch

    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["APP_MAX_FILE_BYTES"] = 1024
    flask_app.config["APP_MAX_FILE_MB"] = 1.0

    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)
    cand = _DummyCandidata()

    with patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand), \
         patch("core.legacy_handlers.validate_upload_file", return_value=(True, b"dep-new", "", {"filename_safe": "dep.jpg"})):
        resp = client.post(
            "/subir_fotos?accion=subir&fila=1",
            data={"depuracion": (io.BytesIO(b"dep-new"), "dep.jpg")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert isinstance(cand.depuracion, (bytes, bytearray))
    assert len(cand.depuracion) > 0


def test_413_handler_returns_user_friendly_message():
    from unittest.mock import patch

    prev_max_content = int(flask_app.config.get("MAX_CONTENT_LENGTH") or 0)
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["MAX_CONTENT_LENGTH"] = 1

    try:
        client = flask_app.test_client()
        assert _login_secretaria(client).status_code in (302, 303)
        cand = _DummyCandidata()
        with patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand):
            resp = client.post(
                "/subir_fotos?accion=subir&fila=1",
                data={"depuracion": (io.BytesIO(b"mucho-contenido"), "depuracion.jpg")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        assert resp.status_code == 200
        assert b"excede el l" in resp.data.lower()
    finally:
        flask_app.config["MAX_CONTENT_LENGTH"] = prev_max_content


def test_js_limit_config_rendered():
    from unittest.mock import patch

    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["APP_MAX_FILE_BYTES"] = 2048
    flask_app.config["APP_MAX_FILE_MB"] = 2.0

    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)
    cand = _DummyCandidata()

    with patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand):
        resp = client.get("/subir_fotos?accion=subir&fila=1", follow_redirects=False)

    assert resp.status_code == 200
    assert b"data-max-bytes=" in resp.data
    assert b"L\xc3\xadmite por archivo" in resp.data
