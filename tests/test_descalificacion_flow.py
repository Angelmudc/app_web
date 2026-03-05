# -*- coding: utf-8 -*-

import io
import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes
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

        self.assertEqual(resp.status_code, 302)
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

    def test_editar_y_subir_archivos_siguen_funcionando_descalificada(self):
        self._login_secretaria()
        cand = _DummyCandidata(fila=1, estado="descalificada")

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

            with patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand), \
                 patch("core.legacy_handlers.validate_upload_file", return_value=(True, b"img", "", {"filename_safe": "a.png"})), \
                 patch("core.legacy_handlers.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/subir_fotos?accion=subir&fila=1",
                    data={"depuracion": (io.BytesIO(b"fake"), "depuracion.png")},
                    content_type="multipart/form-data",
                    follow_redirects=False,
                )
                self.assertEqual(resp.status_code, 302)
                commit_mock.assert_called_once()

    def test_historial_no_se_borra_al_descalificar(self):
        self._login_admin()
        cand = _DummyCandidata(fila=1, estado="lista_para_trabajar")
        prev_historial = list(cand.solicitudes)

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


if __name__ == "__main__":
    unittest.main()
