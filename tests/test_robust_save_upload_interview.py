# -*- coding: utf-8 -*-

import io
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

import core.legacy_handlers as legacy_handlers
from app import app as flask_app
from models import StaffAuditLog
from utils.robust_save import RobustSaveResult, execute_robust_save, legacy_text_is_useful


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
        self.entrevista = ""


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


def test_upload_does_not_overwrite_existing_when_empty():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _DummyCandidata()
    before = cand.depuracion

    with patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand):
        resp = client.post(
            "/subir_fotos?accion=subir&fila=1",
            data={"depuracion": (io.BytesIO(b""), "")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert cand.depuracion == before


def test_upload_saves_and_verifies_bytes():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
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
    assert cand.depuracion == b"dep-new"


def test_interview_legacy_save_verified():
    class _DummySession:
        def flush(self):
            return None

        def commit(self):
            return None

        def rollback(self):
            return None

    candidata = _DummyCandidata()

    def _persist(_attempt: int):
        candidata.entrevista = "Pregunta: Respuesta útil"

    result = execute_robust_save(
        session=_DummySession(),
        persist_fn=_persist,
        verify_fn=lambda: legacy_text_is_useful(candidata.entrevista),
    )

    assert result.ok is True
    assert legacy_text_is_useful(candidata.entrevista) is True


def test_interview_new_create_verified():
    class _DummySession:
        def flush(self):
            return None

        def commit(self):
            return None

        def rollback(self):
            return None

    state = {"count": 0}

    def _persist(_attempt: int):
        state["count"] = 1

    result = execute_robust_save(
        session=_DummySession(),
        persist_fn=_persist,
        verify_fn=lambda: int(state["count"]) > 0,
    )

    assert result.ok is True
    assert result.attempts == 1


def test_retry_on_transient_error():
    class _DummySession:
        def __init__(self):
            self.commit_calls = 0

        def flush(self):
            return None

        def commit(self):
            self.commit_calls += 1
            if self.commit_calls == 1:
                raise OperationalError("select 1", {}, Exception("transient"))
            return None

        def rollback(self):
            return None

    sess = _DummySession()
    result = execute_robust_save(
        session=sess,
        persist_fn=lambda _attempt: None,
        verify_fn=lambda: True,
    )

    assert result.ok is True
    assert result.attempts == 2
    assert sess.commit_calls == 2


def test_failure_shows_error_and_logs():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _DummyCandidata()
    with patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand), \
         patch("core.legacy_handlers.validate_upload_file", return_value=(True, b"dep-new", "", {"filename_safe": "dep.jpg"})), \
         patch("core.legacy_handlers.execute_robust_save", return_value=RobustSaveResult(ok=False, attempts=3, error_message="forced")):
        resp = client.post(
            "/subir_fotos?accion=subir&fila=1",
            data={"depuracion": (io.BytesIO(b"dep-new"), "dep.jpg")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert b"No se pudo guardar" in resp.data

    with flask_app.app_context():
        fail_log = (
            StaffAuditLog.query
            .filter(StaffAuditLog.action_type == "CANDIDATA_UPLOAD_DOCS_SAVE_FAIL")
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        assert fail_log is not None
