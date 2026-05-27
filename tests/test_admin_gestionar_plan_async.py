# -*- coding: utf-8 -*-

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import app as flask_app
import admin.routes as admin_routes


class _SolicitudQueryStub:
    def __init__(self, solicitud):
        self._solicitud = solicitud

    def filter_by(self, **_kwargs):
        return self

    def first_or_404(self):
        return self._solicitud


def _solicitud_stub(*, sol_id=101, cliente_id=7, estado="proceso"):
    now = datetime(2026, 3, 1, 12, 0, 0)
    return SimpleNamespace(
        id=sol_id,
        cliente_id=cliente_id,
        codigo_solicitud=f"SOL-{sol_id}",
        tipo_plan="Básico",
        abono="1000.00",
        payment_cycle_current=1,
        payment_cycle_plan="basico",
        payment_cycle_precio_total="3500.00",
        payment_cycle_abono_requerido="1750.00",
        payment_cycle_estado="pendiente",
        payment_cycle_opened_at=now,
        payment_cycle_closed_at=None,
        payment_cycle_motivo_apertura="seed",
        estado=estado,
        fecha_cancelacion=None,
        motivo_cancelacion=None,
        fecha_inicio_seguimiento=now,
        fecha_ultima_actividad=now,
        fecha_ultima_modificacion=now,
    )


class AdminGestionarPlanAsyncTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self._sync_cycle_plan_result = True
        self._payment_summary = {
            "numero_ciclo": 1,
            "precio_plan": 3500.00,
            "abono_requerido": 1750.00,
            "total_pagado": 0.00,
            "total_abonado": 0.00,
            "saldo_pendiente": 3500.00,
            "plan_norm": "basico",
            "legacy_abono_fallback": False,
            "legacy_abono": 0.00,
            "ciclo_estado": "pendiente",
        }

    def _raw_view(self):
        view = admin_routes.gestionar_plan
        for _ in range(3):
            view = view.__wrapped__
        return view

    def _invoke(self, *, method="POST", data=None, headers=None, path="/admin/clientes/7/solicitudes/101/plan"):
        with flask_app.test_request_context(
            path,
            method=method,
            data=(data or {}),
            headers=(headers or {}),
        ):
            with patch("admin.routes.ensure_current_payment_cycle", return_value=None), \
                 patch("admin.routes.ensure_reactivation_cycle", return_value=None), \
                 patch("admin.routes.sync_cycle_plan_if_no_payments", return_value=self._sync_cycle_plan_result), \
                 patch("admin.routes.get_payment_summary", return_value=self._payment_summary), \
                 patch("admin.routes.crear_pago_solicitud"), \
                 patch("admin.routes.apply_payment_state_from_summary", return_value="espera_pago"):
                rv = self._raw_view()(7, 101)
            if isinstance(rv, tuple):
                resp = rv[0]
                resp.status_code = int(rv[1])
                return resp
            return rv

    def _async_headers(self):
        return {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Admin-Async": "1",
        }

    def test_guardado_async_exitoso_reemplaza_region_local(self):
        solicitud = _solicitud_stub()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={"tipo_plan": "premium", "abono": "1,500"},
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#gestionarPlanAsyncRegion")
        self.assertEqual(data["redirect_url"], "/admin/clientes/7")
        self.assertIsNone(data.get("replace_html"))
        commit_mock.assert_called_once()

    def test_validacion_async_re_renderiza_formulario_con_error_en_campo(self):
        solicitud = _solicitud_stub()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={"tipo_plan": "plan_raro_x"},
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "invalid_input")
        self.assertIn("Tipo de plan inválido", data["replace_html"])
        self.assertIn("is-invalid", data["replace_html"])
        commit_mock.assert_not_called()

    def test_fallback_clasico_post_valido_mantiene_redirect(self):
        solicitud = _solicitud_stub()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={"tipo_plan": "premium", "abono": "1500"},
                    headers={},
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/clientes/7", resp.location)
        commit_mock.assert_called_once()

    def test_fallback_clasico_post_valido_respeta_next_url(self):
        solicitud = _solicitud_stub()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={"tipo_plan": "premium", "abono": "1500"},
                    headers={},
                    path="/admin/clientes/7/solicitudes/101/plan?next=/admin/solicitudes/copiar?page=2",
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/solicitudes/copiar?page=2", resp.location)
        commit_mock.assert_called_once()

    def test_error_async_controlado_devuelve_500_json_limpio(self):
        solicitud = _solicitud_stub()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch("admin.routes.db.session.commit", side_effect=SQLAlchemyError("db_down")):
                resp = self._invoke(
                    data={"tipo_plan": "premium", "abono": "1500"},
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 500)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "server_error")
        self.assertIn("Error de base de datos", data["message"])

    def test_async_conflicto_devuelve_409(self):
        solicitud = _solicitud_stub()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch(
                     "admin.routes.db.session.commit",
                     side_effect=IntegrityError("stmt", "params", Exception("dup")),
                 ):
                resp = self._invoke(
                    data={"tipo_plan": "premium", "abono": "1500"},
                    headers=self._async_headers(),
                )

        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "conflict")

    def test_gestionar_plan_muestra_resumen_automatico_basico(self):
        solicitud = _solicitud_stub()
        solicitud.tipo_plan = "Básico"
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)):
                resp = self._invoke(method="GET", headers=self._async_headers())
        html = resp if isinstance(resp, str) else resp.get_data(as_text=True)
        self.assertIn("RD$ 3,500.00", html)
        self.assertIn("RD$ 1,750.00", html)
        self.assertNotIn("Abono del Cliente", html)

    def test_gestionar_plan_muestra_resumen_automatico_premium_vip(self):
        solicitud = _solicitud_stub()
        solicitud.tipo_plan = "Premium"
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)):
                resp_p = self._invoke(method="GET", headers=self._async_headers())
        solicitud.tipo_plan = "VIP"
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)):
                resp_v = self._invoke(method="GET", headers=self._async_headers())
        html_p = resp_p if isinstance(resp_p, str) else resp_p.get_data(as_text=True)
        html_v = resp_v if isinstance(resp_v, str) else resp_v.get_data(as_text=True)
        self.assertIn("RD$ 5,000.00", html_p)
        self.assertIn("RD$ 2,500.00", html_p)
        self.assertIn("RD$ 8,000.00", html_v)
        self.assertIn("RD$ 4,000.00", html_v)

    def test_gestionar_plan_template_incluye_data_prices_y_targets_resumen(self):
        solicitud = _solicitud_stub()
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)):
                resp = self._invoke(method="GET", headers=self._async_headers())
        html = resp if isinstance(resp, str) else resp.get_data(as_text=True)
        self.assertIn('data-price="3500"', html)
        self.assertIn('data-price="5000"', html)
        self.assertIn('data-price="8000"', html)
        self.assertIn('id="plan-summary-total"', html)
        self.assertIn('id="plan-summary-deposit"', html)
        self.assertIn('id="plan-summary-balance"', html)

    def test_gestionar_plan_basico_emoji_normaliza_correctamente(self):
        solicitud = _solicitud_stub()
        solicitud.tipo_plan = "Básico 💼"
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)):
                resp = self._invoke(method="GET", headers=self._async_headers())
        html = resp if isinstance(resp, str) else resp.get_data(as_text=True)
        self.assertIn("RD$ 3,500.00", html)
        self.assertIn("RD$ 1,750.00", html)

    def test_mensaje_bloqueo_ciclo_actual_con_pagos(self):
        solicitud = _solicitud_stub()
        self._sync_cycle_plan_result = False
        self._payment_summary["total_pagado"] = 1000.00
        self._payment_summary["saldo_pendiente"] = 2500.00
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)):
                resp = self._invoke(
                    data={"tipo_plan": "premium", "manual_override": "0"},
                    headers=self._async_headers(),
                )
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertIn("Este ciclo ya tiene pagos registrados", data["message"])

    def test_get_con_ciclo_pagado_muestra_boton_crear_nuevo_ciclo(self):
        solicitud = _solicitud_stub(estado="activa")
        self._payment_summary["total_pagado"] = 3500.00
        self._payment_summary["saldo_pendiente"] = 0.00
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)):
                resp = self._invoke(method="GET", headers=self._async_headers())
        html = resp if isinstance(resp, str) else resp.get_data(as_text=True)
        self.assertIn("Crear nuevo ciclo de pago", html)
        self.assertIn('data-testid="create-new-payment-cycle"', html)
        self.assertIn('name="plan_action"', html)
        self.assertIn('value="create_new_cycle"', html)

    def test_get_pagada_con_ciclo_pagado_muestra_boton_crear_nuevo_ciclo(self):
        solicitud = _solicitud_stub(estado="pagada")
        self._payment_summary["total_pagado"] = 3500.00
        self._payment_summary["saldo_pendiente"] = 0.00
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)):
                resp = self._invoke(method="GET", headers=self._async_headers())
        html = resp if isinstance(resp, str) else resp.get_data(as_text=True)
        self.assertIn("Crear nuevo ciclo de pago", html)
        self.assertNotIn("DEBUG: can_create_new_cycle", html)

    def test_post_create_new_cycle_ejecuta_accion_y_confirma(self):
        solicitud = _solicitud_stub(estado="activa")
        self._payment_summary["total_pagado"] = 3500.00
        self._payment_summary["saldo_pendiente"] = 0.00
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch("admin.routes.open_new_payment_cycle") as open_cycle_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={"tipo_plan": "premium", "plan_action": "create_new_cycle"},
                    headers=self._async_headers(),
                )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("Nuevo ciclo de pago creado correctamente", data["message"])
        open_cycle_mock.assert_called_once()
        commit_mock.assert_called_once()

    def test_post_create_new_cycle_estado_pagada_con_ciclo_pagado_permitido(self):
        solicitud = _solicitud_stub(estado="pagada")
        self._payment_summary["total_pagado"] = 3500.00
        self._payment_summary["saldo_pendiente"] = 0.00
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch("admin.routes.open_new_payment_cycle") as open_cycle_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={"tipo_plan": "premium", "plan_action": "create_new_cycle"},
                    headers=self._async_headers(),
                )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertNotIn("No corresponde crear un nuevo ciclo en el estado actual", data["message"])
        self.assertIn("Nuevo ciclo de pago creado correctamente", data["message"])
        open_cycle_mock.assert_called_once()
        commit_mock.assert_called_once()

    def test_get_cancelada_oculta_bloqueo_y_muestra_accion_reactivar(self):
        solicitud = _solicitud_stub(estado="cancelada")
        self._payment_summary["total_pagado"] = 1000.00
        self._payment_summary["saldo_pendiente"] = 2500.00
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)):
                resp = self._invoke(method="GET", headers=self._async_headers())
        html = resp if isinstance(resp, str) else resp.get_data(as_text=True)
        self.assertNotIn("Este ciclo ya tiene pagos registrados", html)
        self.assertIn("Reactivar solicitud con nuevo ciclo", html)
        self.assertNotIn("Guardar plan y registrar abono inicial", html)

    def test_post_cancelada_sin_plan_action_reencamina_a_nuevo_ciclo(self):
        solicitud = _solicitud_stub(estado="cancelada")
        self._payment_summary["total_pagado"] = 1000.00
        self._payment_summary["saldo_pendiente"] = 2500.00
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
                 patch("admin.routes.open_new_payment_cycle") as open_cycle_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self._invoke(
                    data={"tipo_plan": "premium"},
                    headers=self._async_headers(),
                )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertNotIn("Este ciclo ya tiene pagos registrados", data["message"])
        open_cycle_mock.assert_called_once()
        commit_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
