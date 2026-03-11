# -*- coding: utf-8 -*-

import io
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

import core.legacy_handlers as legacy_handlers
from app import app as flask_app


class _DummyCandidata:
    def __init__(self):
        self.fila = 1
        self.id = 1
        self.nombre_completo = "Ana Demo"
        self.edad = "30"
        self.numero_telefono = "8090000000"
        self.direccion_completa = "Calle A"
        self.modalidad_trabajo_preferida = "Dormida"
        self.rutas_cercanas = "Centro"
        self.empleo_anterior = "Casa X"
        self.anos_experiencia = "4"
        self.areas_experiencia = "Limpieza"
        self.contactos_referencias_laborales = "Ref Lab"
        self.referencias_familiares_detalle = "Ref Fam Detalle"
        self.referencias_laboral = "Ref Lab"
        self.referencias_familiares = "Ref Fam"
        self.cedula = "001-0000000-1"
        self.sabe_planchar = True
        self.acepta_porcentaje_sueldo = True
        self.disponibilidad_inicio = None
        self.trabaja_con_ninos = None
        self.trabaja_con_mascotas = None
        self.puede_dormir_fuera = None
        self.sueldo_esperado = None
        self.motivacion_trabajo = None
        self.foto_perfil = b"foto-old"
        self.perfil = b"perfil-old"
        self.cedula1 = b"ced1-old"
        self.cedula2 = b"ced2-old"
        self.estado = "inscrita"


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


def test_buscar_edicion_retry_si_commit_falla_una_vez():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _DummyCandidata()
    with patch("core.legacy_handlers.get_candidata_by_id", return_value=cand), \
         patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand), \
         patch(
             "core.legacy_handlers.db.session.commit",
             side_effect=[OperationalError("select 1", {}, Exception("transient")), None],
         ) as commit_mock:
        resp = client.post(
            "/buscar",
            data={
                "guardar_edicion": "1",
                "candidata_id": "1",
                "nombre": "Ana Editada",
            },
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert commit_mock.call_count == 2


def test_buscar_edicion_hace_rollback_si_no_verifica_guardado():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _DummyCandidata()
    with patch("core.legacy_handlers.get_candidata_by_id", return_value=cand), \
         patch("core.legacy_handlers._verify_candidata_fields_saved", return_value=False), \
         patch("core.legacy_handlers.db.session.rollback") as rollback_mock:
        resp = client.post(
            "/buscar",
            data={
                "guardar_edicion": "1",
                "candidata_id": "1",
                "nombre": "Ana Editada",
            },
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert rollback_mock.call_count >= 1
    assert b"Error al guardar" in resp.data


def test_buscar_edicion_actualiza_campos_nuevos_publicos():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _DummyCandidata()
    ok_result = SimpleNamespace(ok=True, attempts=1, error_message="")
    with patch("core.legacy_handlers.get_candidata_by_id", return_value=cand), \
         patch("core.legacy_handlers.execute_robust_save", return_value=ok_result):
        resp = client.post(
            "/buscar",
            data={
                "guardar_edicion": "1",
                "candidata_id": "1",
                "disponibilidad_inicio": "esta_semana",
                "trabaja_con_ninos": "si",
                "trabaja_con_mascotas": "no",
                "puede_dormir_fuera": "si",
                "sueldo_esperado": "RD$30,000",
                "motivacion_trabajo": "Necesita empleo",
            },
            follow_redirects=False,
        )

    assert resp.status_code in (302, 303)
    assert cand.disponibilidad_inicio == "esta_semana"
    assert cand.trabaja_con_ninos is True
    assert cand.trabaja_con_mascotas is False
    assert cand.puede_dormir_fuera is True
    assert cand.sueldo_esperado == "RD$30,000"
    assert cand.motivacion_trabajo == "Necesita empleo"


def test_referencias_no_acepta_placeholders():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _DummyCandidata()
    with patch("core.legacy_handlers.get_candidata_by_id", return_value=cand), \
         patch("core.legacy_handlers.db.session.commit") as commit_mock:
        resp = client.post(
            "/referencias",
            data={
                "candidata_id": "1",
                "referencias_laboral": "none",
                "referencias_familiares": "--",
            },
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert b"Referencias inv" in resp.data
    commit_mock.assert_not_called()


def test_finalizar_proceso_rechaza_archivos_vacios_sin_sobrescribir():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _DummyCandidata()
    before_foto = cand.foto_perfil
    before_ced1 = cand.cedula1
    before_ced2 = cand.cedula2

    class _Query:
        def get(self, _fila):
            return cand

    with patch("core.legacy_handlers.Candidata", new=SimpleNamespace(query=_Query())):
        resp = client.post(
            "/finalizar_proceso?fila=1",
            data={
                "foto_perfil": (io.BytesIO(b""), "foto.jpg"),
                "cedula1": (io.BytesIO(b""), "ced1.jpg"),
                "cedula2": (io.BytesIO(b""), "ced2.jpg"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert b"no pueden estar vac" in resp.data.lower()
    assert cand.foto_perfil == before_foto
    assert cand.cedula1 == before_ced1
    assert cand.cedula2 == before_ced2


def test_verify_interview_new_saved_exige_relacion_candidata():
    entrevista_ok = SimpleNamespace(id=7, candidata_id=99)
    respuestas = [SimpleNamespace(respuesta="Respuesta útil")]

    class _EntrevistaQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return entrevista_ok

    class _RespuestaQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def count(self):
            return len(respuestas)

        def all(self):
            return respuestas

    entrevista_model = type("EntrevistaModel", (), {"id": 1, "query": _EntrevistaQuery()})
    respuesta_model = type("EntrevistaRespuestaModel", (), {"entrevista_id": 1, "query": _RespuestaQuery()})
    with patch("core.legacy_handlers.Entrevista", new=entrevista_model), \
         patch("core.legacy_handlers.EntrevistaRespuesta", new=respuesta_model):
        assert legacy_handlers._verify_interview_new_saved(7, candidata_id=99) is True
        assert legacy_handlers._verify_interview_new_saved(7, candidata_id=1) is False
