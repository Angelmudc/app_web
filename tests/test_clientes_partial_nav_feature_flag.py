# -*- coding: utf-8 -*-

import unittest
import os
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import clientes.routes as clientes_routes


def _unwrap_cliente_view(fn):
    out = fn
    for _ in range(2):  # login_required + cliente_required
        out = out.__wrapped__
    return out


class ClientesPartialNavFeatureFlagTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        self.ayuda_target = _unwrap_cliente_view(clientes_routes.ayuda)
        self._snapshot = {
            "CLIENTES_PARTIAL_NAV_ENABLED": flask_app.config.get("CLIENTES_PARTIAL_NAV_ENABLED"),
            "CLIENTES_PARTIAL_NAV_PILOT_ROUTES": flask_app.config.get("CLIENTES_PARTIAL_NAV_PILOT_ROUTES"),
        }

    def tearDown(self):
        for key, value in self._snapshot.items():
            if value is None and key in flask_app.config:
                flask_app.config.pop(key, None)
            elif value is not None:
                flask_app.config[key] = value

    def test_flag_global_off_por_defecto(self):
        flask_app.config["CLIENTES_PARTIAL_NAV_ENABLED"] = False
        flask_app.config["CLIENTES_PARTIAL_NAV_PILOT_ROUTES"] = ""
        with flask_app.test_request_context("/clientes/ayuda", method="GET"):
            self.assertFalse(clientes_routes._clientes_partial_nav_enabled_for_request())

    def test_flag_global_on_sin_rutas_habilita_todo(self):
        flask_app.config["CLIENTES_PARTIAL_NAV_ENABLED"] = True
        flask_app.config["CLIENTES_PARTIAL_NAV_PILOT_ROUTES"] = ""
        with flask_app.test_request_context("/clientes/planes", method="GET"):
            self.assertTrue(clientes_routes._clientes_partial_nav_enabled_for_request())

    def test_flag_global_on_con_rutas_limita_por_piloto(self):
        flask_app.config["CLIENTES_PARTIAL_NAV_ENABLED"] = True
        flask_app.config["CLIENTES_PARTIAL_NAV_PILOT_ROUTES"] = "/clientes/informacion,/clientes/planes,/clientes/ayuda,/clientes/proceso"
        with flask_app.test_request_context("/clientes/ayuda", method="GET"):
            self.assertTrue(clientes_routes._clientes_partial_nav_enabled_for_request())
        with flask_app.test_request_context("/clientes/dashboard", method="GET"):
            self.assertFalse(clientes_routes._clientes_partial_nav_enabled_for_request())

    def test_base_expone_data_flag_en_shell(self):
        flask_app.config["CLIENTES_PARTIAL_NAV_ENABLED"] = True
        flask_app.config["CLIENTES_PARTIAL_NAV_PILOT_ROUTES"] = "/clientes/ayuda"
        fake_user = SimpleNamespace(id=7, is_authenticated=True, nombre_completo="Cliente Demo", email="demo@example.com")

        with flask_app.app_context():
            with patch("flask_login.utils._get_user", return_value=fake_user):
                with flask_app.test_request_context("/clientes/ayuda", method="GET"):
                    html = self.ayuda_target()

        self.assertIn('data-client-partial-nav-enabled="1"', html)
        self.assertIn('data-client-partial-nav-global="1"', html)
        self.assertIn('id="clientMainViewport"', html)
        self.assertIn('data-client-nav-viewport="true"', html)
        self.assertIn("js/core/client_nav.js", html)

    def test_base_no_carga_client_nav_js_si_flag_esta_off(self):
        flask_app.config["CLIENTES_PARTIAL_NAV_ENABLED"] = False
        flask_app.config["CLIENTES_PARTIAL_NAV_PILOT_ROUTES"] = "/clientes/ayuda"
        fake_user = SimpleNamespace(id=7, is_authenticated=True, nombre_completo="Cliente Demo", email="demo@example.com")

        with flask_app.app_context():
            with patch("flask_login.utils._get_user", return_value=fake_user):
                with flask_app.test_request_context("/clientes/ayuda", method="GET"):
                    html = self.ayuda_target()

        self.assertIn('data-client-partial-nav-enabled="0"', html)
        self.assertNotIn("js/core/client_nav.js", html)

    def test_client_nav_js_contrato_minimo_lifecycle_y_fallback(self):
        js_path = os.path.join(os.getcwd(), "static", "js", "core", "client_nav.js")
        with open(js_path, "r", encoding="utf-8") as f:
            txt = f.read()

        self.assertIn("window.ClientNav", txt)
        self.assertIn("if (window.ClientNav) return;", txt)
        self.assertIn("data-client-partial-nav-enabled", txt)
        self.assertIn("#clientMainViewport", txt)
        self.assertIn("client:navigation-start", txt)
        self.assertIn("client:content-updated", txt)
        self.assertIn("client:navigation-complete", txt)
        self.assertIn("client:navigation-fallback", txt)
        self.assertIn("window.location.assign", txt)
        self.assertIn("resp.status === 401 || resp.status === 403", txt)
        self.assertIn("if (!currentViewport)", txt)
        self.assertIn("if (!nextViewport)", txt)
        self.assertIn("parse_incompatible", txt)
        self.assertIn("redirect_login", txt)
        self.assertIn("a[data-client-nav='true']", txt)
        self.assertIn("form[data-client-nav='true']", txt)
        self.assertIn("history.pushState", txt)
        self.assertIn("window.addEventListener(\"popstate\"", txt)
        self.assertIn("updateCurrentHistoryScrollY", txt)
        self.assertIn("restoreScrollY", txt)
        self.assertIn("applyScrollAndFocus", txt)
        self.assertIn("h1, h2", txt)
        self.assertIn("[data-client-focus-anchor]", txt)
        self.assertIn("window.__clientNavRuntime", txt)
        self.assertIn("client-nav:runtime", txt)
        self.assertIn("successCount", txt)
        self.assertIn("fallbackCount", txt)
        self.assertIn("isFallingBack", txt)
        self.assertIn("if (isFallingBack) return false;", txt)
        self.assertIn("^\\/clientes\\/dashboard\\/?$", txt)
        self.assertIn("^\\/clientes\\/solicitudes\\/?$", txt)
        self.assertIn("^\\/clientes\\/solicitudes\\/\\d+\\/?$", txt)
        self.assertIn("^\\/clientes\\/informacion\\/?$", txt)
        self.assertIn("^\\/clientes\\/planes\\/?$", txt)
        self.assertIn("^\\/clientes\\/ayuda\\/?$", txt)
        self.assertIn("^\\/clientes\\/proceso\\/?$", txt)

    def test_base_links_piloto_opt_in_en_shell_clientes(self):
        tpl_path = os.path.join(os.getcwd(), "templates", "clientes", "base.html")
        with open(tpl_path, "r", encoding="utf-8") as f:
            txt = f.read()

        self.assertIn("url_for('clientes.informacion')", txt)
        self.assertIn("url_for('clientes.planes')", txt)
        self.assertIn("url_for('clientes.ayuda')", txt)
        self.assertIn("url_for('clientes.proceso_contratacion')", txt)
        self.assertIn("url_for('clientes.dashboard')", txt)
        self.assertIn("url_for('clientes.listar_solicitudes')", txt)
        self.assertIn('data-client-nav="true"', txt)

    def test_solicitudes_list_piloto_opt_in_para_get(self):
        tpl_path = os.path.join(os.getcwd(), "templates", "clientes", "solicitudes_list.html")
        with open(tpl_path, "r", encoding="utf-8") as f:
            txt = f.read()

        self.assertIn('class="row g-3 align-items-end solicitudes-filter-form"', txt)
        self.assertIn('method="get"', txt)
        self.assertIn('data-client-nav="true"', txt)
        self.assertIn("url_for('clientes.listar_solicitudes'", txt)
        self.assertIn("data-client-focus-anchor", txt)
        self.assertIn("url_for('clientes.detalle_solicitud', id=s.id)", txt)

    def test_solicitud_detail_piloto_readonly_nav_opt_in(self):
        tpl_path = os.path.join(os.getcwd(), "templates", "clientes", "solicitud_detail.html")
        with open(tpl_path, "r", encoding="utf-8") as f:
            txt = f.read()

        self.assertIn('data-client-focus-anchor', txt)
        self.assertIn("url_for('clientes.listar_solicitudes')", txt)
        self.assertIn('data-client-nav="true"', txt)
        self.assertIn("url_for('clientes.editar_solicitud', id=s.id)", txt)
        self.assertIn("url_for('clientes.cancelar_solicitud', id=s.id)", txt)
        self.assertIn("url_for('clientes.chat_cliente_open')", txt)
        self.assertNotIn("data-client-nav=\"true\" href=\"{{ url_for('clientes.editar_solicitud', id=s.id) }}\"", txt)
        self.assertNotIn("data-client-nav=\"true\" href=\"{{ url_for('clientes.cancelar_solicitud', id=s.id) }}\"", txt)
        self.assertNotIn("form method=\"post\" action=\"{{ url_for('clientes.chat_cliente_open') }}\" class=\"d-inline\" data-client-nav=\"true\"", txt)

    def test_templates_piloto_get_links_tienen_opt_in_client_nav(self):
        checks = [
            (
                "dashboard.html",
                "href=\"{{ url_for('clientes.proceso_contratacion') }}\" class=\"btn btn-outline-secondary btn-sm\" data-client-nav=\"true\"",
            ),
            (
                "dashboard.html",
                "href=\"{{ url_for('clientes.listar_solicitudes') }}\" class=\"btn btn-outline-primary btn-sm\" data-client-nav=\"true\"",
            ),
            (
                "dashboard.html",
                "href=\"{{ url_for('clientes.detalle_solicitud', id=s.id) }}\" data-client-nav=\"true\"",
            ),
            (
                "proceso.html",
                "href=\"{{ url_for('clientes.listar_solicitudes') }}\" class=\"btn btn-primary clientes-proceso-btn\" data-client-nav=\"true\"",
            ),
            (
                "proceso.html",
                "href=\"{{ url_for('clientes.ayuda') }}\" class=\"btn btn-outline-secondary clientes-proceso-btn\" data-client-nav=\"true\"",
            ),
            (
                "informacion.html",
                "href=\"{{ url_for('clientes.planes') }}\" class=\"btn btn-outline-primary\" data-client-nav=\"true\"",
            ),
            (
                "informacion.html",
                "href=\"{{ url_for('clientes.ayuda') }}\" class=\"btn btn-outline-secondary\" data-client-nav=\"true\"",
            ),
            (
                "planes.html",
                "href=\"{{ url_for('clientes.ayuda') }}\" class=\"btn btn-outline-secondary\" data-client-nav=\"true\"",
            ),
            (
                "planes.html",
                "href=\"{{ url_for('clientes.dashboard') }}\" class=\"btn btn-outline-secondary\" data-client-nav=\"true\"",
            ),
        ]
        for filename, expected in checks:
            tpl_path = os.path.join(os.getcwd(), "templates", "clientes", filename)
            with open(tpl_path, "r", encoding="utf-8") as f:
                txt = f.read()
            self.assertIn(expected, txt)

    def test_flujos_piloto_solicitados_quedan_interceptables(self):
        def _read(relpath):
            with open(os.path.join(os.getcwd(), relpath), "r", encoding="utf-8") as f:
                return f.read()

        dashboard = _read("templates/clientes/dashboard.html")
        informacion = _read("templates/clientes/informacion.html")
        base = _read("templates/clientes/base.html")

        self.assertIn(
            "href=\"{{ url_for('clientes.proceso_contratacion') }}\" class=\"btn btn-outline-secondary btn-sm\" data-client-nav=\"true\"",
            dashboard,
        )
        self.assertIn(
            "href=\"{{ url_for('clientes.planes') }}\" class=\"btn btn-outline-primary\" data-client-nav=\"true\"",
            informacion,
        )
        self.assertIn(
            "data-client-nav=\"true\"\n               href=\"{{ url_for('clientes.dashboard') }}\">Inicio</a>",
            base,
        )
        self.assertIn(
            "data-client-nav=\"true\"\n               href=\"{{ url_for('clientes.informacion') }}\">Información</a>",
            base,
        )
        self.assertIn(
            "data-client-nav=\"true\"\n               href=\"{{ url_for('clientes.proceso_contratacion') }}\">Cómo funciona</a>",
            base,
        )

    def test_clientes_js_hardening_lifecycle_e_idempotencia_base(self):
        js_path = os.path.join(os.getcwd(), "static", "clientes", "js", "clientes.js")
        with open(js_path, "r", encoding="utf-8") as f:
            txt = f.read()

        self.assertIn("function initShell()", txt)
        self.assertIn("function mountViewport(root)", txt)
        self.assertIn("function getViewportRoot()", txt)
        self.assertIn("client:navigation-complete", txt)
        self.assertIn("data-clientes-solicitud-bound", txt)
        self.assertIn("data-clientes-autosave-bound", txt)
        self.assertIn("data-clientes-autosize-bound", txt)
        self.assertIn("RUNTIME.beforeUnloadBound", txt)
        self.assertIn("w.ClientesPortal.mount", txt)
        self.assertIn("if (evt && evt.detail && evt.detail.bootstrap) return;", txt)


if __name__ == "__main__":
    unittest.main()
