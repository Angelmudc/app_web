# -*- coding: utf-8 -*-

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime

from app import app as flask_app
import core.legacy_handlers as legacy_handlers


class _Pagination:
    def __init__(self, items):
        self.items = items
        self.pages = 1
        self.has_prev = False
        self.has_next = False
        self.prev_num = 1
        self.next_num = 1


class _CandidataListQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self._exclude_trabajando = False
        self._exclude_descalificada = False

    def filter(self, *criteria):
        for crit in criteria:
            try:
                params = crit.compile().params
            except Exception:
                params = {}
            for val in params.values():
                if val == "trabajando":
                    self._exclude_trabajando = True
                if val == "descalificada":
                    self._exclude_descalificada = True
        return self

    def order_by(self, *args, **kwargs):
        return self

    def paginate(self, *args, **kwargs):
        out = list(self.rows)
        if self._exclude_trabajando:
            out = [x for x in out if getattr(x, "estado", None) != "trabajando"]
        if self._exclude_descalificada:
            out = [x for x in out if getattr(x, "estado", None) != "descalificada"]
        return _Pagination(out)


class BancoExcludesTrabajandoTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        login_resp = self.client.post(
            "/admin/login",
            data={"usuario": "Karla", "clave": "9989"},
            follow_redirects=False,
        )
        if login_resp.status_code == 429:
            with self.client.session_transaction() as sess:
                sess["usuario"] = "Karla"
                sess["role"] = "secretaria"
                sess["logged_at"] = datetime.utcnow().isoformat(timespec="seconds")
                sess["is_admin_session"] = True
        else:
            self.assertIn(login_resp.status_code, (302, 303))

    def test_banco_excluye_trabajando_y_muestra_lista(self):
        cand_trab = SimpleNamespace(fila=1, nombre_completo="Trabajando", cedula="1", estado="trabajando", nota_descalificacion=None)
        cand_lista = SimpleNamespace(fila=2, nombre_completo="Lista", cedula="2", estado="lista_para_trabajar", nota_descalificacion=None)
        query = _CandidataListQuery([cand_trab, cand_lista])

        with flask_app.app_context():
            with patch.object(legacy_handlers.Candidata, "query", query), \
                 patch("core.legacy_handlers.render_template", side_effect=lambda tpl, **ctx: ",".join(x.nombre_completo for x in (ctx.get("candidatas") or []))):
                resp = self.client.get("/candidatas", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Lista", html)
        self.assertNotIn("Trabajando", html)


if __name__ == "__main__":
    unittest.main()
