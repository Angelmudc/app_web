# -*- coding: utf-8 -*-

import time
import unittest

from flask import make_response, request

import clientes.routes as clientes_routes
from app import app as flask_app


class ClientesBaselineMedicionTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True

    def tearDown(self):
        flask_app.config.pop("CLIENTES_BASELINE_ENABLED", None)

    def test_aplica_headers_baseline_cuando_esta_habilitado(self):
        flask_app.config["CLIENTES_BASELINE_ENABLED"] = True

        with flask_app.test_request_context("/clientes/ayuda", method="GET"):
            request.environ["_clientes_baseline_started_at"] = time.perf_counter() - 0.01
            response = make_response("<html>ok</html>", 200)

            response = clientes_routes._apply_clientes_baseline_headers(response)

        self.assertIn("X-Clientes-Baseline-Scope", response.headers)
        self.assertIn("X-Clientes-Baseline-Latency-Ms", response.headers)
        self.assertIn("X-Clientes-Baseline-HTML-Bytes", response.headers)
        self.assertGreater(float(response.headers["X-Clientes-Baseline-Latency-Ms"]), 0.0)
        self.assertGreater(int(response.headers["X-Clientes-Baseline-HTML-Bytes"]), 0)

    def test_no_aplica_headers_si_flag_esta_apagado(self):
        flask_app.config["CLIENTES_BASELINE_ENABLED"] = False

        with flask_app.test_request_context("/clientes/ayuda", method="GET"):
            request.environ["_clientes_baseline_started_at"] = time.perf_counter() - 0.01
            response = make_response("<html>ok</html>", 200)

            response = clientes_routes._apply_clientes_baseline_headers(response)

        self.assertNotIn("X-Clientes-Baseline-Scope", response.headers)
        self.assertNotIn("X-Clientes-Baseline-Latency-Ms", response.headers)
        self.assertNotIn("X-Clientes-Baseline-HTML-Bytes", response.headers)


if __name__ == "__main__":
    unittest.main()
