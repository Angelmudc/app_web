# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _SolicitudQueryByObject:
    def __init__(self, obj):
        self._obj = obj

    def options(self, *_args, **_kwargs):
        return self

    def get_or_404(self, _id):
        return self._obj


class _ReemplazoQueryByObject:
    def __init__(self, obj):
        self._obj = obj

    def filter_by(self, **_kwargs):
        return self

    def first_or_404(self):
        return self._obj


class _CandidataFilterResult:
    def __init__(self, obj):
        self._obj = obj

    def first(self):
        return self._obj


class _CandidataQueryStub:
    def __init__(self, rows):
        self._rows = list(rows)
        self._by_id = {int(getattr(r, "fila", 0) or 0): r for r in self._rows}

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)

    def get(self, row_id):
        try:
            rid = int(row_id or 0)
        except Exception:
            rid = 0
        return self._by_id.get(rid)

    def filter_by(self, **kwargs):
        fila = int(kwargs.get("fila") or 0)
        return _CandidataFilterResult(self._by_id.get(fila))


class _FakeSelectField:
    def __init__(self, data=0, label="Asignar candidata (reemplazo)"):
        self.data = data
        self.choices = []
        self.errors = []
        self.label = SimpleNamespace(text=label)

    def __call__(self, *args, **kwargs):
        _ = (args, kwargs)
        return ""


class _FakeTextAreaField:
    def __init__(self, data=""):
        self.data = data
        self.errors = []
        self.label = SimpleNamespace(text="Notas sobre el reemplazo")

    def __call__(self, *args, **kwargs):
        _ = (args, kwargs)
        return ""


class _FakeReemplazoFinForm:
    def __init__(self):
        self.candidata_new_id = _FakeSelectField(data=2)
        self.nota_adicional = _FakeTextAreaField(data="nota interna")
        self.errors = {}

    def hidden_tag(self):
        return ""

    def validate_on_submit(self):
        return True


def _solicitud_stub():
    return SimpleNamespace(
        id=10,
        codigo_solicitud="SOL-010",
        cliente_id=7,
        candidata=None,
        candidata_id=1,
        estado="reemplazo",
        sueldo="10000",
        fecha_ultima_actividad=None,
        fecha_ultima_modificacion=None,
        reemplazos=[],
    )


def _reemplazo_stub():
    return SimpleNamespace(
        id=20,
        solicitud_id=10,
        candidata_old_id=1,
        candidata_new_id=None,
        estado_previo_solicitud="proceso",
        fecha_fin_reemplazo=None,
        nota_adicional=None,
    )


def _candidata_stub(row_id, nombre, estado="lista_para_trabajar"):
    return SimpleNamespace(
        fila=row_id,
        nombre_completo=nombre,
        cedula=f"001-000000{row_id}-0",
        codigo=f"COD-{row_id}",
        numero_telefono=f"809000000{row_id}",
        estado=estado,
        monto_total=None,
        porciento=None,
        fecha_de_pago=None,
        fecha_ultima_modificacion=None,
    )


class AdminReemplazoFinAsyncTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        login = self.client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))

    def _async_headers(self):
        return {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Admin-Async": "1",
        }

    def test_render_clasico_intacto(self):
        sol = _solicitud_stub()
        repl = _reemplazo_stub()
        c1 = _candidata_stub(1, "Ana Perez")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryByObject(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQueryByObject(repl)), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQueryStub([c1])):
                resp = self.client.get("/admin/solicitudes/10/reemplazos/20/finalizar", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="reemplazoFinSearchRegion"', html)
        self.assertIn('id="reemplazoFinPickRegion"', html)
        self.assertIn("Finalizar reemplazo", html)
        self.assertIn('name="row_version"', html)
        self.assertIn('name="idempotency_key"', html)

    def test_busqueda_async_devuelve_pick_region(self):
        sol = _solicitud_stub()
        repl = _reemplazo_stub()
        c1 = _candidata_stub(1, "Ana Perez")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryByObject(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQueryByObject(repl)), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQueryStub([c1])):
                resp = self.client.get(
                    "/admin/solicitudes/10/reemplazos/20/finalizar?q=ana",
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#reemplazoFinPickRegion")
        self.assertIn("Ana Perez", data["replace_html"])
        self.assertIn("Selecciona una doméstica", data["replace_html"])

    def test_fallback_clasico_de_busqueda_se_mantiene(self):
        sol = _solicitud_stub()
        repl = _reemplazo_stub()
        c1 = _candidata_stub(1, "Ana Perez")
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryByObject(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQueryByObject(repl)), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQueryStub([c1])):
                resp = self.client.get(
                    "/admin/solicitudes/10/reemplazos/20/finalizar?q=ana",
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Ana Perez", html)
        self.assertIn("data-admin-async-form", html)

    def test_post_final_clasico_sin_regresion(self):
        sol = _solicitud_stub()
        repl = _reemplazo_stub()
        cand_old = _candidata_stub(1, "Candidata Old", estado="trabajando")
        cand_new = _candidata_stub(2, "Candidata New", estado="lista_para_trabajar")
        cand_query = _CandidataQueryStub([cand_old, cand_new])

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryByObject(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQueryByObject(repl)), \
                 patch.object(admin_routes.Candidata, "query", cand_query), \
                 patch("admin.routes.AdminReemplazoFinForm", _FakeReemplazoFinForm), \
                 patch("admin.routes.assert_candidata_no_descalificada", return_value=None), \
                 patch("admin.routes._sync_solicitud_candidatas_after_assignment") as sync_mock, \
                 patch(
                     "admin.routes._mark_candidata_estado",
                     side_effect=lambda c, estado, **_kwargs: setattr(c, "estado", estado),
                 ), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/20/finalizar",
                    data={"q": ""},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/clientes/7", resp.location)
        self.assertEqual(repl.candidata_new_id, 2)
        self.assertEqual(repl.nota_adicional, "nota interna")
        self.assertIsNotNone(repl.fecha_fin_reemplazo)
        self.assertEqual(sol.candidata_id, 2)
        self.assertEqual(sol.estado, "proceso")
        self.assertEqual(cand_new.estado, "trabajando")
        self.assertEqual(cand_new.monto_total, Decimal("10000.00"))
        self.assertEqual(cand_new.porciento, Decimal("2500.00"))
        sync_mock.assert_called_once()
        self.assertGreaterEqual(commit_mock.call_count, 1)

    def test_post_final_rehidrata_choice_seleccionada_con_q_vacio(self):
        sol = _solicitud_stub()
        repl = _reemplazo_stub()
        cand_old = _candidata_stub(1, "Candidata Old", estado="trabajando")
        cand_new = _candidata_stub(2, "Candidata New", estado="lista_para_trabajar")
        cand_query = _CandidataQueryStub([cand_old, cand_new])

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryByObject(sol)), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQueryByObject(repl)), \
                 patch.object(admin_routes.Candidata, "query", cand_query), \
                 patch("admin.routes._search_candidatas_reemplazo", return_value=[]), \
                 patch("admin.routes.assert_candidata_no_descalificada", return_value=None), \
                 patch("admin.routes._sync_solicitud_candidatas_after_assignment"), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/20/finalizar",
                    data={
                        "q": "",
                        "candidata_new_id": "2",
                        "nota_adicional": "nota",
                    },
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/clientes/7", resp.location)
        self.assertEqual(repl.candidata_new_id, 2)
        self.assertGreaterEqual(commit_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
