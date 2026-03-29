# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _DummyCliente:
    nombre_completo = "Cliente Demo"


class _DummySolicitud:
    id = 10
    cliente_id = 7
    codigo_solicitud = "SOL-010"
    cliente = _DummyCliente()
    estado = "activa"
    fecha_solicitud = datetime.utcnow()
    reemplazos = []


class _DummyCandidata:
    def __init__(self, fila=101):
        self.fila = fila
        self.nombre_completo = f"Cand {fila}"
        self.cedula = "000-0000000-0"
        self.numero_telefono = "8090000000"
        self.codigo = f"C-{fila}"


class _SolicitudQuery:
    def options(self, *args, **kwargs):
        return self

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return _DummySolicitud()


class _SolicitudCandidataQuery:
    def filter_by(self, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return []


class AdminMatchingSendAsyncTest(unittest.TestCase):
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
        self.assertIn(login_resp.status_code, (302, 303))

    def _post_ui(self, data=None):
        return self.client.post(
            "/admin/matching/solicitudes/10/enviar/ui",
            data=(data or {"candidata_ids": ["101"]}),
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "X-Admin-Async": "1",
            },
            follow_redirects=False,
        )

    def _assert_payload_base(self, payload):
        self.assertIsInstance(payload, dict)
        self.assertIn("success", payload)
        self.assertIn("message", payload)
        self.assertIn("category", payload)
        self.assertIn("error_code", payload)
        self.assertEqual(payload.get("update_target"), "#matchingDetalleAsyncRegion")
        self.assertEqual(payload.get("redirect_url"), "/admin/matching/solicitudes/10")

    def test_ui_async_success(self):
        ranked = [{
            "candidate": _DummyCandidata(101),
            "score": 88,
            "level": "alta",
            "summary": "ok",
            "risks": [],
            "reasons": [],
            "breakdown": [],
            "breakdown_snapshot": {"ready_check": {"ready": True}},
        }]
        with flask_app.app_context():
            with patch("admin.routes._matching_send_candidatas_result", return_value={
                "success": True,
                "message": "Candidata enviada al cliente. Total procesadas: 1.",
                "category": "success",
                "error_code": None,
                "status_code": 200,
            }), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())):
                resp = self._post_ui()
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json() or {}
        self._assert_payload_base(payload)
        self.assertTrue(payload.get("success"))
        self.assertEqual(payload.get("category"), "success")
        self.assertIn("replace_html", payload)
        self.assertIn("Checklist listo para enviar", payload.get("replace_html") or "")

    def test_ui_async_error_no_selection(self):
        ranked = [{
            "candidate": _DummyCandidata(101),
            "score": 80,
            "level": "alta",
            "summary": "ok",
            "risks": [],
            "reasons": [],
            "breakdown": [],
            "breakdown_snapshot": {"ready_check": {"ready": True}},
        }]
        with flask_app.app_context():
            with patch("admin.routes._matching_send_candidatas_result", return_value={
                "success": False,
                "message": "Selecciona al menos una candidata para enviar.",
                "category": "warning",
                "error_code": "no_selection",
                "status_code": 200,
            }), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())):
                resp = self._post_ui(data={})
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json() or {}
        self._assert_payload_base(payload)
        self.assertFalse(payload.get("success"))
        self.assertEqual(payload.get("error_code"), "no_selection")

    def test_ui_async_error_blocked_other_client(self):
        ranked = [{
            "candidate": _DummyCandidata(101),
            "score": 80,
            "level": "alta",
            "summary": "ok",
            "risks": [],
            "reasons": [],
            "breakdown": [],
            "breakdown_snapshot": {"ready_check": {"ready": True}},
        }]
        with flask_app.app_context():
            with patch("admin.routes._matching_send_candidatas_result", return_value={
                "success": False,
                "message": "Bloqueada en otro cliente.",
                "category": "danger",
                "error_code": "blocked_other_client",
                "status_code": 200,
            }), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())):
                resp = self._post_ui()
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json() or {}
        self._assert_payload_base(payload)
        self.assertEqual(payload.get("error_code"), "blocked_other_client")

    def test_ui_async_error_rejected_without_force(self):
        ranked = [{
            "candidate": _DummyCandidata(101),
            "score": 80,
            "level": "alta",
            "summary": "ok",
            "risks": [],
            "reasons": [],
            "breakdown": [],
            "breakdown_snapshot": {"ready_check": {"ready": True}},
        }]
        with flask_app.app_context():
            with patch("admin.routes._matching_send_candidatas_result", return_value={
                "success": False,
                "message": "Rechazada sin force_send.",
                "category": "warning",
                "error_code": "rejected_without_force",
                "status_code": 200,
            }), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())):
                resp = self._post_ui()
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json() or {}
        self._assert_payload_base(payload)
        self.assertEqual(payload.get("error_code"), "rejected_without_force")

    def test_ui_async_error_not_ready(self):
        ranked = [{
            "candidate": _DummyCandidata(101),
            "score": 80,
            "level": "alta",
            "summary": "ok",
            "risks": [],
            "reasons": [],
            "breakdown": [],
            "breakdown_snapshot": {"ready_check": {"ready": True}},
        }]
        with flask_app.app_context():
            with patch("admin.routes._matching_send_candidatas_result", return_value={
                "success": False,
                "message": "No está lista.",
                "category": "danger",
                "error_code": "not_ready",
                "status_code": 200,
            }), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())):
                resp = self._post_ui()
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json() or {}
        self._assert_payload_base(payload)
        self.assertEqual(payload.get("error_code"), "not_ready")

    def test_ui_async_error_disqualified(self):
        ranked = [{
            "candidate": _DummyCandidata(101),
            "score": 80,
            "level": "alta",
            "summary": "ok",
            "risks": [],
            "reasons": [],
            "breakdown": [],
            "breakdown_snapshot": {"ready_check": {"ready": True}},
        }]
        with flask_app.app_context():
            with patch("admin.routes._matching_send_candidatas_result", return_value={
                "success": False,
                "message": "Candidata descalificada.",
                "category": "danger",
                "error_code": "disqualified",
                "status_code": 200,
            }), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())):
                resp = self._post_ui()
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json() or {}
        self._assert_payload_base(payload)
        self.assertEqual(payload.get("error_code"), "disqualified")

    def test_ui_async_error_conflict_devuelve_409(self):
        ranked = [{
            "candidate": _DummyCandidata(101),
            "score": 80,
            "level": "alta",
            "summary": "ok",
            "risks": [],
            "reasons": [],
            "breakdown": [],
            "breakdown_snapshot": {"ready_check": {"ready": True}},
        }]
        with flask_app.app_context():
            with patch("admin.routes._matching_send_candidatas_result", return_value={
                "success": False,
                "message": "Conflicto detectado.",
                "category": "warning",
                "error_code": "conflict",
                "status_code": 409,
            }), \
                 patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())):
                resp = self._post_ui()
        self.assertEqual(resp.status_code, 409)
        payload = resp.get_json() or {}
        self._assert_payload_base(payload)
        self.assertFalse(payload.get("success"))
        self.assertEqual(payload.get("error_code"), "conflict")


if __name__ == "__main__":
    unittest.main()
