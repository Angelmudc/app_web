# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _SolicitudQueryStub:
    def __init__(self, rows, *, by_id=None, by_cliente=None):
        self._rows = list(rows)
        self._by_id = by_id
        self._by_cliente = by_cliente

    def _clone(self, **updates):
        return _SolicitudQueryStub(
            self._rows,
            by_id=updates.get("by_id", self._by_id),
            by_cliente=updates.get("by_cliente", self._by_cliente),
        )

    def filter_by(self, **kwargs):
        return self._clone(
            by_id=kwargs.get("id", self._by_id),
            by_cliente=kwargs.get("cliente_id", self._by_cliente),
        )

    def _filtered(self):
        rows = list(self._rows)
        if self._by_id is not None:
            rows = [r for r in rows if int(getattr(r, "id", 0) or 0) == int(self._by_id)]
        if self._by_cliente is not None:
            rows = [r for r in rows if int(getattr(r, "cliente_id", 0) or 0) == int(self._by_cliente)]
        return rows

    def first_or_404(self):
        rows = self._filtered()
        if rows:
            return rows[0]
        raise AssertionError("Solicitud no encontrada")


def _solicitud_stub():
    now = datetime(2026, 3, 1, 10, 0, 0)
    return SimpleNamespace(
        id=10,
        cliente_id=7,
        codigo_solicitud="SOL-010",
        tipo_servicio="DOMESTICA_LIMPIEZA",
        ciudad_sector="Santiago",
        rutas_cercanas="Ruta K",
        modalidad_trabajo="Con dormida",
        horario="8-5",
        experiencia="Experiencia base",
        edad_requerida=["26-35"],
        funciones=["limpieza"],
        funciones_otro="",
        tipo_lugar="casa",
        habitaciones=2,
        banos=1,
        dos_pisos=False,
        areas_comunes=["sala"],
        area_otro="",
        adultos=2,
        ninos=0,
        edades_ninos="",
        mascota="",
        sueldo="18000",
        pasaje_aporte=False,
        nota_cliente="",
        detalles_servicio=None,
        fecha_ultima_modificacion=now,
        solicitud_tipo_servicio_label="Doméstica general",
    )


class AdminEditarSolicitudAsyncTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    def _async_headers(self):
        return {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Admin-Async": "1",
        }

    def _raw_view(self):
        view = admin_routes.editar_solicitud_admin
        for _ in range(3):
            view = view.__wrapped__
        return view

    def _invoke(self, *, data=None, headers=None):
        with flask_app.test_request_context(
            "/admin/clientes/7/solicitudes/10/editar",
            method="POST",
            data=(data or {}),
            headers=(headers or {}),
        ):
            rv = self._raw_view()(7, 10)
            if isinstance(rv, tuple):
                resp = rv[0]
                resp.status_code = int(rv[1])
                return resp
            return rv

    def test_editar_solicitud_async_exito_devuelve_redirect_url(self):
        solicitud = _solicitud_stub()
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes._execute_form_save", return_value=SimpleNamespace(ok=True, error_message="")), \
                 patch("admin.routes._audit_log"):
                resp = self._invoke(
                    data={
                        "_async_target": "#editarSolicitudAsyncRegion",
                        "tipo_servicio": "DOMESTICA_LIMPIEZA",
                        "ciudad_sector": "Santiago / Centro",
                        "modalidad_trabajo": "Con dormida",
                        "experiencia": "Experiencia comprobada",
                        "horario": "L-V",
                        "tipo_lugar": "casa",
                        "habitaciones": "2",
                        "banos": "1",
                        "adultos": "2",
                        "sueldo": "18000",
                        "pasaje_aporte": "0",
                    },
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIsNone(data["update_target"])
        self.assertEqual(data["redirect_url"], "/admin/clientes/7#clienteSolicitudesAsyncScope")

    def test_editar_solicitud_async_error_validacion_inline(self):
        solicitud = _solicitud_stub()
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])):
                resp = self._invoke(
                    data={"_async_target": "#editarSolicitudAsyncRegion"},
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "invalid_input")
        self.assertEqual(data["update_target"], "#editarSolicitudAsyncRegion")
        self.assertIn('id="solicitud-form"', data.get("replace_html", ""))
        self.assertIn("is-invalid", data.get("replace_html", ""))

    def test_editar_solicitud_async_error_preserva_modalidad_guiada(self):
        solicitud = _solicitud_stub()
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])):
                resp = self._invoke(
                    data={
                        "_async_target": "#editarSolicitudAsyncRegion",
                        "modalidad_grupo": "con_salida_diaria",
                        "modalidad_especifica": "Salida diaria otro",
                        "modalidad_otro_text": "lunes a viernes",
                    },
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["success"])
        html = data.get("replace_html", "")
        self.assertIn('name="modalidad_grupo" value="con_salida_diaria"', html)
        self.assertIn('name="modalidad_grupo" value="con_salida_diaria" required', html)
        self.assertIn('value="con_salida_diaria" required aria-required="true" checked', html)
        self.assertIn('<option value="Salida diaria otro" selected>Salida diaria otro</option>', html)
        self.assertIn('id="modalidad_otro_text"', html)
        self.assertIn('value="lunes a viernes"', html)

    def test_editar_solicitud_fallback_clasico_intacto(self):
        solicitud = _solicitud_stub()
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes._execute_form_save", return_value=SimpleNamespace(ok=True, error_message="")), \
                 patch("admin.routes._audit_log"):
                resp = self._invoke(
                    data={
                        "tipo_servicio": "DOMESTICA_LIMPIEZA",
                        "ciudad_sector": "Santiago / Centro",
                        "modalidad_trabajo": "Con dormida",
                        "experiencia": "Experiencia comprobada",
                        "horario": "L-V",
                        "tipo_lugar": "casa",
                        "habitaciones": "2",
                        "banos": "1",
                        "adultos": "2",
                        "sueldo": "18000",
                        "pasaje_aporte": "0",
                    },
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/clientes/7", resp.location)

    def test_editar_solicitud_guarda_campos_otro_aun_sin_flag_otro_en_post(self):
        solicitud = _solicitud_stub()

        def _run_save(*, persist_fn, **_kwargs):
            persist_fn(1)
            return SimpleNamespace(ok=True, error_message="")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes._execute_form_save", side_effect=_run_save), \
                 patch("admin.routes._audit_log"):
                resp = self._invoke(
                    data={
                        "_async_target": "#editarSolicitudAsyncRegion",
                        "tipo_servicio": "DOMESTICA_LIMPIEZA",
                        "ciudad_sector": "Santiago / Centro",
                        "modalidad_trabajo": "Con dormida",
                        "experiencia": "Experiencia comprobada",
                        "horario": "L-V",
                        "tipo_lugar": "casa",
                        "habitaciones": "2",
                        "banos": "1",
                        "adultos": "2",
                        "sueldo": "18000",
                        "pasaje_aporte": "0",
                        "funciones": ["limpieza"],
                        "funciones_otro": "planchar cristales",
                        "areas_comunes": ["sala"],
                        "area_otro": "balcon trasero",
                        "edad_requerida": ["26-35"],
                        "edad_otro": "30 a 40",
                        "tipo_lugar": "casa",
                        "tipo_lugar_otro": "villa",
                    },
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(solicitud.funciones_otro, "planchar cristales")
        self.assertEqual(solicitud.area_otro, "balcon trasero")
        self.assertIn("30 a 40", solicitud.edad_requerida or [])
        self.assertEqual(solicitud.tipo_lugar, "villa")

    def test_editar_solicitud_limpia_estructura_hogar_si_no_hay_limpieza(self):
        solicitud = _solicitud_stub()
        solicitud.tipo_lugar = "casa"
        solicitud.habitaciones = 4
        solicitud.banos = 2
        solicitud.dos_pisos = True
        solicitud.areas_comunes = ["sala", "comedor"]
        solicitud.area_otro = "balcon"

        def _run_save(*, persist_fn, **_kwargs):
            persist_fn(1)
            return SimpleNamespace(ok=True, error_message="")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes._execute_form_save", side_effect=_run_save), \
                 patch("admin.routes._audit_log"):
                resp = self._invoke(
                    data={
                        "_async_target": "#editarSolicitudAsyncRegion",
                        "tipo_servicio": "DOMESTICA_LIMPIEZA",
                        "ciudad_sector": "Santiago / Centro",
                        "modalidad_trabajo": "Con dormida",
                        "experiencia": "Experiencia comprobada",
                        "horario": "L-V",
                        "adultos": "2",
                        "sueldo": "18000",
                        "pasaje_aporte": "0",
                        "funciones": ["cocinar"],
                    },
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(solicitud.tipo_lugar, None)
        self.assertEqual(solicitud.habitaciones, None)
        self.assertEqual(solicitud.banos, None)
        self.assertEqual(solicitud.dos_pisos, False)
        self.assertEqual(solicitud.areas_comunes, [])
        self.assertEqual(solicitud.area_otro, None)

    def test_rehidratacion_api_en_partial_compartido(self):
        path = os.path.join(os.getcwd(), "templates", "clientes", "_solicitud_form_fields.html")
        with open(path, "r", encoding="utf-8") as fh:
            txt = fh.read()
        self.assertIn("window.SolicitudSharedFields", txt)
        self.assertIn("window.SolicitudSharedFields.init", txt)
        self.assertIn("modalidad_trabajo_hidden", txt)
        self.assertIn("publicSolicitudNuevaForm", txt)
        self.assertNotIn("window.__solicitudSharedFieldsInit__", txt)


if __name__ == "__main__":
    unittest.main()
