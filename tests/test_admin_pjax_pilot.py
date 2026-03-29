# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _SolicitudQueryStub:
    def __init__(self, rows):
        self._rows = list(rows)

    def options(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def outerjoin(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def filter_by(self, **_kwargs):
        return self

    def offset(self, value):
        try:
            n = int(value)
        except Exception:
            n = 0
        clone = _SolicitudQueryStub(self._rows)
        clone._rows = self._rows[max(0, n):]
        return clone

    def limit(self, value):
        try:
            n = int(value)
        except Exception:
            n = len(self._rows)
        clone = _SolicitudQueryStub(self._rows[:max(0, n)])
        return clone

    def count(self):
        return len(self._rows)

    def all(self):
        return list(self._rows)


def _solicitud_stub(sol_id=10, cliente_id=7):
    return SimpleNamespace(
        id=sol_id,
        codigo_solicitud=f"SOL-{sol_id}",
        cliente_id=cliente_id,
        cliente=SimpleNamespace(nombre_completo=f"Cliente {cliente_id}"),
        estado="activa",
        candidata=None,
        candidata_id=None,
        fecha_solicitud=datetime(2026, 3, 1, 10, 0, 0),
        ciudad_sector="Santiago",
        reemplazos=[],
        last_copiado_at=None,
        rutas_cercanas="Monumental",
        estado_previo_espera_pago=None,
        fecha_cambio_espera_pago=None,
        usuario_cambio_espera_pago=None,
        fecha_ultima_actividad=datetime(2026, 3, 1, 10, 0, 0),
        fecha_ultima_modificacion=datetime(2026, 3, 1, 10, 0, 0),
    )


class AdminPjaxPilotTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        login = self.client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))

    def test_shell_viewport_estable_en_base(self):
        tpl_path = os.path.join(os.getcwd(), "templates", "base.html")
        with open(tpl_path, "r", encoding="utf-8") as f:
            txt = f.read()
        self.assertIn('id="adminMainViewport"', txt)
        self.assertIn('data-admin-nav-viewport="true"', txt)
        self.assertIn("js/core/admin_nav.js", txt)
        self.assertIn("js/admin/solicitud_detail_ui.js", txt)

    def test_links_piloto_opt_in_en_templates_objetivo(self):
        solicitudes_tpl = os.path.join(os.getcwd(), "templates", "admin", "_solicitudes_list_results.html")
        with open(solicitudes_tpl, "r", encoding="utf-8") as f:
            solicitudes_txt = f.read()
        self.assertIn("url_for('admin.detalle_cliente', cliente_id=s.cliente_id)", solicitudes_txt)
        self.assertIn("url_for('admin.detalle_solicitud', cliente_id=s.cliente_id, id=s.id)", solicitudes_txt)
        self.assertIn('data-admin-nav="true"', solicitudes_txt)

        cliente_tpl = os.path.join(os.getcwd(), "templates", "admin", "_cliente_detail_summary_region.html")
        with open(cliente_tpl, "r", encoding="utf-8") as f:
            cliente_txt = f.read()
        self.assertIn("url_for('admin.listar_solicitudes')", cliente_txt)
        self.assertIn('data-admin-nav="true"', cliente_txt)

        cliente_detail_tpl = os.path.join(os.getcwd(), "templates", "admin", "cliente_detail.html")
        with open(cliente_detail_tpl, "r", encoding="utf-8") as f:
            cliente_detail_txt = f.read()
        self.assertIn("url_for('admin.detalle_solicitud', cliente_id=cliente.id, id=s.id)", cliente_detail_txt)
        self.assertIn('data-admin-nav="true"', cliente_detail_txt)

        solicitud_summary_tpl = os.path.join(os.getcwd(), "templates", "admin", "_solicitud_detail_summary_region.html")
        with open(solicitud_summary_tpl, "r", encoding="utf-8") as f:
            solicitud_summary_txt = f.read()
        self.assertIn("url_for('admin.detalle_cliente', cliente_id=solicitud.cliente_id)", solicitud_summary_txt)
        self.assertIn('data-admin-nav="true"', solicitud_summary_txt)

    def test_motor_admin_nav_cubre_fallback_historial_titulo(self):
        js_path = os.path.join(os.getcwd(), "static", "js", "core", "admin_nav.js")
        with open(js_path, "r", encoding="utf-8") as f:
            txt = f.read()

        self.assertIn("history.pushState", txt)
        self.assertIn("window.addEventListener(\"popstate\"", txt)
        self.assertIn("document.title", txt)
        self.assertIn("admin:navigation-complete", txt)
        self.assertIn("admin:content-updated", txt)
        self.assertIn("window.location.assign", txt)
        self.assertIn("resp.status === 401 || resp.status === 403", txt)
        self.assertIn("if (!nextViewport)", txt)
        self.assertIn("if (!currentViewport)", txt)
        self.assertIn("^\\/admin\\/solicitudes\\/?$", txt)
        self.assertIn("^\\/admin\\/clientes\\/\\d+\\/?$", txt)
        self.assertIn("^\\/admin\\/clientes\\/\\d+\\/solicitudes\\/\\d+\\/?$", txt)
        self.assertIn("updateCurrentHistoryScrollY", txt)
        self.assertIn("restoreScrollY", txt)
        self.assertIn("admin:navigation-fallback", txt)
        self.assertIn("console.warn(\"[AdminNav:fallback]\"", txt)
        self.assertIn("is-admin-nav-loading", txt)
        self.assertIn("h1, h2", txt)
        self.assertIn("[data-admin-focus-anchor]", txt)

    def test_feedback_visual_minimo_css_para_pjax(self):
        css_path = os.path.join(os.getcwd(), "static", "css", "base.css")
        with open(css_path, "r", encoding="utf-8") as f:
            css_txt = f.read()
        self.assertIn("#adminNavProgressBar", css_txt)
        self.assertIn("#adminNavProgressBar.is-active", css_txt)
        self.assertIn("is-admin-nav-loading", css_txt)

    def test_templates_piloto_tienen_focus_anchor(self):
        solicitudes_tpl = os.path.join(os.getcwd(), "templates", "admin", "solicitudes_list.html")
        with open(solicitudes_tpl, "r", encoding="utf-8") as f:
            solicitudes_txt = f.read()
        self.assertIn("data-admin-focus-anchor", solicitudes_txt)

        cliente_tpl = os.path.join(os.getcwd(), "templates", "admin", "cliente_detail.html")
        with open(cliente_tpl, "r", encoding="utf-8") as f:
            cliente_txt = f.read()
        self.assertIn("data-admin-focus-anchor", cliente_txt)

        solicitud_tpl = os.path.join(os.getcwd(), "templates", "admin", "solicitud_detail.html")
        with open(solicitud_tpl, "r", encoding="utf-8") as f:
            solicitud_txt = f.read()
        self.assertIn("data-admin-focus-anchor", solicitud_txt)

    def test_solicitud_detail_js_extraido_e_idempotente(self):
        tpl_path = os.path.join(os.getcwd(), "templates", "admin", "solicitud_detail.html")
        with open(tpl_path, "r", encoding="utf-8") as f:
            tpl_txt = f.read()
        self.assertNotIn("document.addEventListener('DOMContentLoaded'", tpl_txt)
        self.assertNotIn("function copiarResumenCliente()", tpl_txt)
        self.assertIn("js-copy-resumen-cliente", tpl_txt)
        self.assertIn("url_for('admin.detalle_cliente', cliente_id=solicitud.cliente_id)", tpl_txt)
        self.assertIn('data-admin-nav="true"', tpl_txt)

        js_path = os.path.join(os.getcwd(), "static", "js", "admin", "solicitud_detail_ui.js")
        with open(js_path, "r", encoding="utf-8") as f:
            js_txt = f.read()
        self.assertIn("window.AdminSolicitudDetailUI", js_txt)
        self.assertIn("function boot(root)", js_txt)
        self.assertIn("document.addEventListener(\"DOMContentLoaded\", init", js_txt)
        self.assertIn("document.addEventListener(\"admin:navigation-complete\"", js_txt)
        self.assertIn("document.addEventListener(\"admin:content-updated\"", js_txt)
        self.assertIn("if (btn.dataset.copyBound === \"1\") return;", js_txt)

    def test_ssr_clasico_de_solicitudes_permanece_intacto(self):
        rows = [_solicitud_stub(sol_id=10, cliente_id=7)]
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(rows)):
                resp = self.client.get("/admin/solicitudes", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("<title>Panel de Solicitudes</title>", html)
        self.assertIn('href="/admin/clientes/7"', html)
        self.assertIn('data-admin-nav="true"', html)
        self.assertIn('id="adminMainViewport"', html)
