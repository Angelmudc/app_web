# -*- coding: utf-8 -*-

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import make_response

import clientes.routes as clientes_routes
from app import app as flask_app


class ClientesCachePolicyHeadersTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True

    def _fake_user(self, *, authed: bool):
        return SimpleNamespace(is_authenticated=bool(authed))

    def test_html_get_autenticado_usa_private_revalidate(self):
        with patch.object(clientes_routes, "current_user", self._fake_user(authed=True)):
            with flask_app.test_request_context("/clientes/ayuda", method="GET"):
                response = make_response("<html>ok</html>", 200)
                response = clientes_routes._apply_clientes_cache_policy(response)

        self.assertEqual(response.headers.get("Cache-Control"), "private, no-cache, must-revalidate, max-age=0")
        self.assertEqual(response.headers.get("Pragma"), "no-cache")
        self.assertEqual(response.headers.get("Expires"), "0")

    def test_mutacion_post_permanece_no_store_estricto(self):
        with patch.object(clientes_routes, "current_user", self._fake_user(authed=True)):
            with flask_app.test_request_context("/clientes/solicitudes/1/cancelar", method="POST"):
                response = make_response("ok", 200)
                response = clientes_routes._apply_clientes_cache_policy(response)

        self.assertEqual(response.headers.get("Cache-Control"), "no-store, no-cache, must-revalidate, max-age=0")
        self.assertEqual(response.headers.get("Pragma"), "no-cache")
        self.assertEqual(response.headers.get("Expires"), "0")

    def test_realtime_stream_usa_no_cache(self):
        with patch.object(clientes_routes, "current_user", self._fake_user(authed=True)):
            with flask_app.test_request_context("/clientes/live/invalidation/stream", method="GET"):
                response = make_response("event: heartbeat\n\n", 200)
                response.headers["Content-Type"] = "text/event-stream; charset=utf-8"
                response = clientes_routes._apply_clientes_cache_policy(response)

        self.assertEqual(response.headers.get("Cache-Control"), "no-cache, must-revalidate, max-age=0")
        self.assertEqual(response.headers.get("Pragma"), "no-cache")
        self.assertEqual(response.headers.get("Expires"), "0")

    def test_publico_sensible_get_permanece_no_store(self):
        with patch.object(clientes_routes, "current_user", self._fake_user(authed=False)):
            with flask_app.test_request_context("/clientes/solicitudes/publica/tok123", method="GET"):
                response = make_response("<html>public</html>", 200)
                response = clientes_routes._apply_clientes_cache_policy(response)

        self.assertEqual(response.headers.get("Cache-Control"), "no-store, no-cache, must-revalidate, max-age=0")
        self.assertEqual(response.headers.get("Pragma"), "no-cache")
        self.assertEqual(response.headers.get("Expires"), "0")

    def test_resp_con_cache_explicito_no_se_sobrescribe(self):
        with patch.object(clientes_routes, "current_user", self._fake_user(authed=True)):
            with flask_app.test_request_context("/clientes/domesticas/1/foto_perfil", method="GET"):
                response = make_response("img", 200)
                response.headers["Cache-Control"] = "private, max-age=3600"
                response.headers["Pragma"] = "private"
                response = clientes_routes._apply_clientes_cache_policy(response)

        self.assertEqual(response.headers.get("Cache-Control"), "private, max-age=3600")
        self.assertEqual(response.headers.get("Pragma"), "private")


if __name__ == "__main__":
    unittest.main()
