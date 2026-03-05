# -*- coding: utf-8 -*-

import unittest
from types import SimpleNamespace

from flask import Flask
from werkzeug.exceptions import Forbidden

from utils.guards import is_candidata_descalificada, require_not_descalificada


class GuardsDescalificacionTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test-secret"

        @self.app.route("/destino")
        def destino():
            return "ok"

    def test_is_candidata_descalificada_por_estado_o_flag(self):
        cand_estado = SimpleNamespace(estado="descalificada", is_descalificada=False)
        cand_flag = SimpleNamespace(estado="lista_para_trabajar", is_descalificada=True)
        cand_ok = SimpleNamespace(estado="lista_para_trabajar", is_descalificada=False)

        self.assertTrue(is_candidata_descalificada(cand_estado))
        self.assertTrue(is_candidata_descalificada(cand_flag))
        self.assertFalse(is_candidata_descalificada(cand_ok))

    def test_require_not_descalificada_bloquea_accion_operativa(self):
        cand = SimpleNamespace(fila=1, nombre_completo="Ana", estado="descalificada")

        with self.app.test_request_context("/x"):
            with self.assertRaises(Forbidden):
                require_not_descalificada(cand, action_name="matching")

    def test_require_not_descalificada_permite_editar_y_redirecciona(self):
        cand = SimpleNamespace(fila=1, nombre_completo="Ana", estado="descalificada")

        with self.app.test_request_context("/x"):
            allowed = require_not_descalificada(cand, action_name="editar_candidata")
            self.assertIsNone(allowed)

        with self.app.test_request_context("/x"):
            resp = require_not_descalificada(
                cand,
                action_name="matching",
                redirect_endpoint="destino",
            )
            self.assertEqual(resp.status_code, 302)
            self.assertTrue(resp.location.endswith("/destino"))


if __name__ == "__main__":
    unittest.main()
