# -*- coding: utf-8 -*-

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import clientes.routes as clientes_routes


def _unwrap_cliente_view(fn, n):
    out = fn
    for _ in range(n):
        out = out.__wrapped__
    return out


class _SolicitudQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter_by(self, **kwargs):
        rows = self._rows
        for k, v in kwargs.items():
            rows = [r for r in rows if getattr(r, k, None) == v]
        return _SolicitudQuery(rows)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, n):
        n = int(n or 0)
        return _SolicitudQuery(self._rows[:n])

    def all(self):
        return list(self._rows)

    def count(self):
        return len(self._rows)


class _EstadoAggQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def group_by(self, *args, **kwargs):
        return self

    def all(self):
        counts = {}
        for row in self._rows:
            estado = (getattr(row, "estado", None) or "sin_definir")
            counts[estado] = counts.get(estado, 0) + 1
        return list(counts.items())


class ClientesSolicitudBorradorResumeTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.fake_user = SimpleNamespace(
            id=7,
            is_authenticated=True,
            role="cliente",
            codigo="CL-007",
            nombre_completo="Cliente Demo",
        )
        self.nueva_target = _unwrap_cliente_view(clientes_routes.nueva_solicitud, 3)
        self.dashboard_target = _unwrap_cliente_view(clientes_routes.dashboard, 2)

    def test_save_draft_post_stores_payload_and_redirects_without_creating(self):
        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", self.fake_user), \
                 patch.object(clientes_routes, "_cache_ok", return_value=False), \
                 patch("clientes.routes.db.session.add") as add_mock:
                with flask_app.test_request_context(
                    "/clientes/solicitudes/nueva",
                    method="POST",
                    data={
                        "save_draft": "1",
                        "wizard_step": "3",
                        "ciudad_sector": "Santiago / Centro",
                        "modalidad_trabajo": "Salida diaria - lunes a viernes",
                    },
                ):
                    resp = self.nueva_target()
                    self.assertEqual(resp.status_code, 302)
                    self.assertIn("/clientes/solicitudes/nueva?continuar=1", resp.location)
                    bucket = dict(clientes_routes.session.get("_cliente_solicitud_drafts") or {})
                    saved = bucket.get(str(self.fake_user.id)) or {}
                    payload = saved.get("payload") or {}
                    self.assertEqual(payload.get("ciudad_sector"), "Santiago / Centro")
                    self.assertIn("modalidad_trabajo", payload)
                    self.assertEqual(payload.get("wizard_step"), "3")
                    add_mock.assert_not_called()

    def test_get_restores_draft_into_form_and_view_context(self):
        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", self.fake_user), \
                 patch.object(clientes_routes, "_cache_ok", return_value=False), \
                 patch.object(clientes_routes, "render_template", side_effect=lambda template, **ctx: ctx):
                with flask_app.test_request_context("/clientes/solicitudes/nueva", method="GET"):
                    clientes_routes.session["_cliente_solicitud_drafts"] = {
                        str(self.fake_user.id): {
                            "saved_at": "2026-04-07T15:30:00Z",
                            "payload": {
                                "ciudad_sector": "Santiago / Jardines",
                                "modalidad_trabajo": "Con dormida - lunes a viernes",
                                "modalidad_grupo": "con_dormida",
                                "modalidad_especifica": "Con dormida 💤 lunes a viernes",
                                "pasaje_mode": "otro",
                                "pasaje_otro_text": "Mitad y mitad",
                                "wizard_step": "3",
                            },
                        }
                    }
                    ctx = self.nueva_target()
                    form = ctx["form"]
                    self.assertTrue(ctx["draft_restored"])
                    self.assertEqual(form.ciudad_sector.data, "Santiago / Jardines")
                    self.assertEqual(ctx["public_modalidad_group"], "con_dormida")
                    self.assertEqual(ctx["public_pasaje_mode"], "otro")
                    self.assertEqual(ctx["public_pasaje_otro"], "Mitad y mitad")
                    self.assertEqual(ctx["initial_wizard_step"], 3)

    def test_dashboard_receives_draft_meta_for_continue_cta(self):
        rows = []
        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", self.fake_user), \
                 patch.object(clientes_routes.Solicitud, "query", _SolicitudQuery(rows)), \
                 patch("clientes.routes.db.session.query", return_value=_EstadoAggQuery(rows)), \
                 patch.object(clientes_routes, "_cliente_solicitud_draft_meta", return_value={"saved_at": datetime(2026, 4, 7, 10, 0, 0), "continue_url": "/clientes/solicitudes/nueva?continuar=1"}), \
                 patch.object(clientes_routes, "render_template", side_effect=lambda template, **ctx: ctx):
                with flask_app.test_request_context("/clientes/dashboard", method="GET"):
                    ctx = self.dashboard_target()
                    self.assertIn("solicitud_draft", ctx)
                    self.assertIsNotNone(ctx["solicitud_draft"])
                    self.assertIn("continue_url", ctx["solicitud_draft"])


if __name__ == "__main__":
    unittest.main()
