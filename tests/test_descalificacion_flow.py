# -*- coding: utf-8 -*-

import io
import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from werkzeug.exceptions import NotFound

from app import app as flask_app
import admin.routes as admin_routes
import clientes.routes as clientes_routes
import core.legacy_handlers as legacy_handlers


class _DummyCandidata:
    def __init__(self, fila=1, estado="lista_para_trabajar"):
        self.fila = fila
        self.nombre_completo = "Ana Perez"
        self.edad = "31"
        self.direccion_completa = "Santiago"
        self.modalidad_trabajo_preferida = "salida diaria"
        self.rutas_cercanas = "Cienfuegos"
        self.empleo_anterior = "Casa"
        self.anos_experiencia = "4"
        self.areas_experiencia = "Limpieza"
        self.contactos_referencias_laborales = "Ref 1"
        self.referencias_familiares_detalle = "Ref fam"
        self.sabe_planchar = False
        self.acepta_porcentaje_sueldo = True
        self.estado = estado
        self.nota_descalificacion = None
        self.fecha_cambio_estado = None
        self.usuario_cambio_estado = None
        self.cedula = "001-0000000-1"
        self.codigo = "C-001"
        self.numero_telefono = "8091112222"
        self.solicitudes = [SimpleNamespace(id=99)]


class _PaginationStub:
    def __init__(self, items):
        self.items = items
        self.pages = 1
        self.has_prev = False
        self.has_next = False
        self.prev_num = 1
        self.next_num = 1


class _DescalificacionListQuery:
    def __init__(self, cand):
        self.cand = cand

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def paginate(self, *args, **kwargs):
        return _PaginationStub([self.cand])


class _CandidataAdminQuery:
    def __init__(self, cand):
        self.cand = cand

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return self.cand


class _DummySolicitud:
    id = 10
    cliente_id = 7
    codigo_solicitud = "SOL-010"
    estado = "activa"
    fecha_solicitud = datetime.utcnow()
    candidata_id = None
    sueldo = None


class _SolicitudQuery:
    def options(self, *args, **kwargs):
        return self

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return _DummySolicitud()


class _DummyField:
    def __init__(self, data=None):
        self.data = data
        self.choices = []


class _FakePagoForm:
    def __init__(self):
        self.candidata_id = _DummyField(1)
        self.monto_pagado = _DummyField("1000")

    def validate_on_submit(self):
        return True


class _CandidataPagoQuery:
    def __init__(self, cand):
        self.cand = cand

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return [self.cand]

    def get(self, _id):
        return self.cand


class _CandidataMatchingQuery:
    def __init__(self, cand):
        self.cand = cand
        self._mode = "all"

    def filter(self, *args, **kwargs):
        self._mode = "filter"
        return self

    def all(self):
        return [self.cand]

    def filter_by(self, **kwargs):
        self._mode = "filter_by"
        return self

    def first(self):
        return self.cand


class _Preg:
    id = 1


class _OneCandidataQuery:
    def __init__(self, cand):
        self.cand = cand

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return self.cand


class _DummyQuery:
    def __init__(self, obj):
        self._obj = obj

    def get_or_404(self, _fila):
        return self._obj


class _ScalarQueryStub:
    def __init__(self, value):
        self._value = value

    def filter(self, *args, **kwargs):
        return self

    def scalar(self):
        return self._value


class DescalificacionFlowTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    def _login_admin(self):
        resp = self.client.post(
            "/admin/login",
            data={"usuario": "Cruz", "clave": "8998"},
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, (302, 303))

    def _login_secretaria(self):
        resp = self.client.post(
            "/admin/login",
            data={"usuario": "Karla", "clave": "9989"},
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, (302, 303))

    def test_admin_puede_descalificar_y_reactivar(self):
        self._login_admin()
        cand = _DummyCandidata(fila=1, estado="lista_para_trabajar")

        with flask_app.app_context():
            with patch.object(admin_routes.Candidata, "query", _CandidataAdminQuery(cand)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/candidatas/1/descalificar",
                    data={"motivo": "Documentos inconsistentes", "next": "/buscar?candidata_id=1"},
                    follow_redirects=False,
                )
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(cand.estado, "descalificada")
                self.assertEqual(cand.nota_descalificacion, "Documentos inconsistentes")
                commit_mock.assert_called_once()

            with patch.object(admin_routes.Candidata, "query", _CandidataAdminQuery(cand)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/candidatas/1/reactivar",
                    data={"next": "/buscar?candidata_id=1"},
                    follow_redirects=False,
                )
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(cand.estado, "lista_para_trabajar")
                self.assertIsNone(cand.nota_descalificacion)
                commit_mock.assert_called_once()

    def test_secretaria_no_puede_descalificar_ni_reactivar(self):
        self._login_secretaria()

        resp1 = self.client.post(
            "/admin/candidatas/1/descalificar",
            data={"motivo": "x"},
            follow_redirects=False,
        )
        resp2 = self.client.post(
            "/admin/candidatas/1/reactivar",
            data={},
            follow_redirects=False,
        )
        self.assertEqual(resp1.status_code, 403)
        self.assertEqual(resp2.status_code, 403)

    def test_no_permita_asignar_descalificada_en_pago(self):
        self._login_admin()
        cand = _DummyCandidata(fila=1, estado="descalificada")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.Candidata, "query", _CandidataPagoQuery(cand)), \
                 patch("admin.routes.AdminPagoForm", _FakePagoForm), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/clientes/7/solicitudes/10/pago",
                    data={"csrf_token": "ok"},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 302)
        commit_mock.assert_not_called()

    def test_no_permite_enviar_a_cliente_si_descalificada(self):
        self._login_admin()
        cand = _DummyCandidata(fila=1, estado="descalificada")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.Candidata, "query", _CandidataMatchingQuery(cand)), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())), \
                 patch("admin.routes.rank_candidates", return_value=[]), \
                 patch("admin.routes.db.session.add") as add_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"candidata_ids": ["1"], "csrf_token": "ok"},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 403)
        self.assertIn(b"descalificada", resp.data.lower())
        add_mock.assert_not_called()
        commit_mock.assert_not_called()

    def test_no_permite_guardar_entrevista_nueva_si_descalificada(self):
        self._login_secretaria()
        cand = _DummyCandidata(fila=1, estado="descalificada")

        with flask_app.app_context():
            with patch("core.legacy_handlers._get_candidata_safe_by_pk", return_value=cand), \
                 patch("core.legacy_handlers._get_preguntas_db_por_tipo", return_value=[_Preg()]), \
                 patch("core.legacy_handlers.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/entrevistas/nueva/1/domestica",
                    data={"q_1": "respuesta", "csrf_token": "ok"},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 302)
        commit_mock.assert_not_called()

    def test_no_permite_compat_en_candidata_descalificada(self):
        self._login_secretaria()
        cand = _DummyCandidata(fila=1, estado="descalificada")

        with flask_app.app_context():
            with patch.object(legacy_handlers.Candidata, "query", _DummyQuery(cand)):
                resp = self.client.get(
                    "/secretarias/compat/candidata?fila=1",
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 302)

    def test_editar_y_subir_archivos_siguen_funcionando_descalificada(self):
        self._login_secretaria()
        cand = _DummyCandidata(fila=1, estado="descalificada")
        cand.perfil = b"\x89PNG\r\n\x1a\nabc"

        with flask_app.app_context():
            with patch("core.legacy_handlers.get_candidata_by_id", return_value=cand), \
                 patch("core.legacy_handlers.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/buscar",
                    data={
                        "guardar_edicion": "1",
                        "candidata_id": "1",
                        "nombre": "Ana Perez Editada",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(resp.status_code, 302)
                commit_mock.assert_called_once()

            with patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand):
                resp = self.client.get("/subir_fotos/imagen/1/perfil", follow_redirects=False)
                self.assertEqual(resp.status_code, 200)

            with patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand), \
                 patch("core.legacy_handlers.validate_upload_file", return_value=(True, b"binario", "", {"filename_safe": "doc.jpg"})), \
                 patch("core.legacy_handlers.db.session.commit") as commit_upload:
                resp = self.client.post(
                    "/subir_fotos?accion=subir&fila=1",
                    data={"depuracion": (io.BytesIO(b"file"), "depuracion.jpg")},
                    content_type="multipart/form-data",
                    follow_redirects=False,
                )
                self.assertEqual(resp.status_code, 302)
                commit_upload.assert_called_once()

            with patch("core.legacy_handlers._get_candidata_safe_by_pk", return_value=cand), \
                 patch("core.legacy_handlers.render_template", return_value="OK"):
                resp = self.client.get("/candidata/perfil?fila=1", follow_redirects=False)
                self.assertEqual(resp.status_code, 200)

    def test_modulo_descalificacion_visible_en_admin_y_reactivacion_solo_admin(self):
        cand = _DummyCandidata(fila=1, estado="descalificada")
        cand.nota_descalificacion = "Motivo demo"

        self._login_admin()
        home_resp = self.client.get("/home", follow_redirects=False)
        self.assertEqual(home_resp.status_code, 200)
        self.assertIn(b"Descalificaci", home_resp.data)

        with flask_app.app_context():
            with patch.object(admin_routes.Candidata, "query", _DescalificacionListQuery(cand)):
                resp = self.client.get("/admin/candidatas/descalificacion?q=ana", follow_redirects=False)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Descalificaci", resp.data)
        self.assertIn(b"Reactivar", resp.data)

        self._login_secretaria()
        with flask_app.app_context():
            with patch.object(admin_routes.Candidata, "query", _DescalificacionListQuery(cand)):
                resp = self.client.get("/admin/candidatas/descalificacion?q=ana", follow_redirects=False)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"Reactivar", resp.data)

    def test_no_aparece_en_detalle_publico_si_descalificada(self):
        cand = _DummyCandidata(fila=1, estado="descalificada")

        detail_target = clientes_routes.domestica_detalle
        for _ in range(4):
            detail_target = detail_target.__wrapped__

        with flask_app.app_context():
            with patch.object(clientes_routes.Candidata, "query", _OneCandidataQuery(cand)):
                with flask_app.test_request_context("/clientes/domesticas/1", method="GET"):
                    with self.assertRaises(NotFound):
                        detail_target(1)

    def test_historial_no_se_borra_al_descalificar(self):
        self._login_admin()
        cand = _DummyCandidata(fila=1, estado="lista_para_trabajar")
        cand.inscripcion = True
        cand.entrevista = "Texto"
        cand.perfil = b"perfil"
        cand.depuracion = b"dep"
        prev_historial = list(cand.solicitudes)
        snapshot = {
            "codigo": cand.codigo,
            "cedula": cand.cedula,
            "inscripcion": cand.inscripcion,
            "entrevista": cand.entrevista,
            "perfil": cand.perfil,
            "depuracion": cand.depuracion,
        }

        with flask_app.app_context():
            with patch.object(admin_routes.Candidata, "query", _CandidataAdminQuery(cand)), \
                 patch("admin.routes.db.session.commit"):
                resp = self.client.post(
                    "/admin/candidatas/1/descalificar",
                    data={"motivo": "No acepta condiciones"},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(cand.solicitudes), len(prev_historial))
        self.assertEqual(cand.solicitudes[0].id, prev_historial[0].id)
        self.assertEqual(cand.codigo, snapshot["codigo"])
        self.assertEqual(cand.cedula, snapshot["cedula"])
        self.assertEqual(cand.inscripcion, snapshot["inscripcion"])
        self.assertEqual(cand.entrevista, snapshot["entrevista"])
        self.assertEqual(cand.perfil, snapshot["perfil"])
        self.assertEqual(cand.depuracion, snapshot["depuracion"])

    def test_secretaria_no_puede_confirmar_eliminacion_definitiva(self):
        self._login_secretaria()
        cand = _DummyCandidata(fila=1, estado="lista_para_trabajar")

        with flask_app.app_context():
            with patch("core.legacy_handlers.db.session.get", return_value=cand), \
                 patch("core.legacy_handlers.db.session.delete") as delete_mock, \
                 patch("core.legacy_handlers.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/candidatas/eliminar",
                    data={
                        "confirmar_eliminacion": "1",
                        "candidata_id": "1",
                        "busqueda": "ana",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Solo admin puede confirmar la eliminaci", resp.data)
        delete_mock.assert_not_called()
        commit_mock.assert_not_called()

    def test_eliminacion_bloqueada_si_tiene_historial(self):
        self._login_admin()
        cand = _DummyCandidata(fila=1, estado="lista_para_trabajar")
        cand.solicitudes = [SimpleNamespace(id=10)]
        cand.llamadas = []

        with flask_app.app_context():
            with patch("core.legacy_handlers.db.session.get", return_value=cand), \
                 patch("core.legacy_handlers.db.session.delete") as delete_mock, \
                 patch("core.legacy_handlers.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/candidatas/eliminar",
                    data={
                        "confirmar_eliminacion": "1",
                        "candidata_id": "1",
                        "busqueda": "ana",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"No se puede eliminar esta candidata porque tiene historial", resp.data)
        delete_mock.assert_not_called()
        commit_mock.assert_not_called()

    def test_eliminacion_admin_sin_historial_llama_delete(self):
        self._login_admin()
        cand = _DummyCandidata(fila=1, estado="lista_para_trabajar")
        cand.solicitudes = []
        cand.llamadas = []

        with flask_app.app_context():
            with patch("core.legacy_handlers.db.session.get", return_value=cand), \
                 patch("core.legacy_handlers.db.session.query", return_value=_ScalarQueryStub(0)), \
                 patch("core.legacy_handlers.db.session.delete") as delete_mock, \
                 patch("core.legacy_handlers.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/candidatas/eliminar",
                    data={
                        "confirmar_eliminacion": "1",
                        "candidata_id": "1",
                        "busqueda": "ana",
                    },
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        delete_mock.assert_called_once_with(cand)
        commit_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
