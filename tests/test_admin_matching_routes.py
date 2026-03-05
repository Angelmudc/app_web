# -*- coding: utf-8 -*-

import os
import re
import unittest
from datetime import datetime
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


class _DummyCandidata:
    def __init__(self, fila: int):
        self.fila = fila
        self.nombre_completo = f"Cand {fila}"
        self.cedula = "000-0000000-0"
        self.numero_telefono = "8090000000"
        self.codigo = f"C-{fila}"
        self.estado = "lista_para_trabajar"


class _SolicitudQuery:
    def options(self, *args, **kwargs):
        return self

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return _DummySolicitud()


class _CandidataQuery:
    def __init__(self):
        self.kwargs = {}
        self._ids = []

    def filter_by(self, **kwargs):
        self.kwargs = kwargs
        return self

    def filter(self, *args, **kwargs):
        # Para tests no evaluamos expresiones SQL; devolvemos candidatas solicitadas.
        return self

    def all(self):
        if self._ids:
            return [_DummyCandidata(i) for i in self._ids]
        fila = self.kwargs.get("fila")
        if fila is None:
            return []
        return [_DummyCandidata(int(fila))]

    def first(self):
        fila = int(self.kwargs.get("fila"))
        return _DummyCandidata(fila)


class _SolicitudCandidataQuery:
    def __init__(self, existing=None, all_rows=None):
        self._existing = existing or {}
        self._all_rows = all_rows or []
        self.kwargs = {}

    def filter_by(self, **kwargs):
        self.kwargs = kwargs
        return self

    def first(self):
        key = (self.kwargs.get("solicitud_id"), self.kwargs.get("candidata_id"))
        return self._existing.get(key)

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        solicitud_id = self.kwargs.get("solicitud_id")
        if solicitud_id is None:
            return list(self._all_rows)
        return [r for r in self._all_rows if getattr(r, "solicitud_id", None) == solicitud_id]


class _ExistingSolicitudCandidata:
    def __init__(self, solicitud_id, candidata_id, status="descartada"):
        self.solicitud_id = solicitud_id
        self.candidata_id = candidata_id
        self.status = status
        self.score_snapshot = 0
        self.breakdown_snapshot = {}
        self.created_by = None
        self.id = 77
        self.created_at = datetime.utcnow()
        self.candidata = _DummyCandidata(candidata_id)


class _DummyNotificacion:
    def __init__(self, payload=None):
        self.payload = payload or {}
        self.titulo = ""
        self.cuerpo = ""
        self.updated_at = None


class _ClienteNotificacionQuery:
    def __init__(self, existing=None):
        self.existing = existing

    def filter_by(self, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.existing


class _SyncSCQuery:
    def __init__(self, rows):
        self.rows = rows
        self.kwargs = {}

    def filter_by(self, **kwargs):
        self.kwargs = kwargs
        return self

    def first(self):
        for row in self.rows:
            ok = True
            for k, v in self.kwargs.items():
                if getattr(row, k, None) != v:
                    ok = False
                    break
            if ok:
                return row
        return None

    def all(self):
        out = []
        for row in self.rows:
            ok = True
            for k, v in self.kwargs.items():
                if getattr(row, k, None) != v:
                    ok = False
                    break
            if ok:
                out.append(row)
        return out


class AdminMatchingRoutesTest(unittest.TestCase):
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

    def test_post_enviar_creates_relations(self):
        ranked = [
            {
                "candidate": _DummyCandidata(101),
                "score": 88,
                "level": "alta",
                "summary": "ok",
                "risks": [],
                "breakdown": [{"title": "Horario", "score": 23, "notes": "ok"}],
                "breakdown_snapshot": {
                    "city_detectada": "Ciudad detectada: Santiago ✅",
                    "tokens_match": "Tokens coinciden: villa, maria, santiago",
                    "rutas_match": "Rutas: cienfuegos",
                    "modalidad_match": "Modalidad compatible",
                    "horario_match": "Horario compatible: 8am-5pm",
                    "skills_match": "Habilidades/funciones: limpieza",
                    "mascota_penalty": "Sin penalizacion por mascotas",
                    "test_bonus": "Bonus test: +12",
                },
            },
            {
                "candidate": _DummyCandidata(102),
                "score": 75,
                "level": "alta",
                "summary": "ok",
                "risks": [],
                "breakdown": [{"title": "Horario", "score": 20, "notes": "ok"}],
                "breakdown_snapshot": {
                    "city_detectada": "Ciudad detectada: Santiago ✅",
                    "tokens_match": "Tokens coinciden: santiago",
                    "rutas_match": "Rutas: av x",
                    "modalidad_match": "Modalidad parcialmente compatible",
                    "horario_match": "Horario compatible: 8am-5pm",
                    "skills_match": "Habilidades/funciones: limpieza",
                    "mascota_penalty": "Sin penalizacion por mascotas",
                    "test_bonus": "Bonus test: +9",
                },
            },
        ]

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch.object(admin_routes.ClienteNotificacion, "query", _ClienteNotificacionQuery()), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes.db.session.add") as add_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"candidata_ids": ["101", "102"]},
                    follow_redirects=False,
                )

            self.assertEqual(resp.status_code, 302)
            self.assertEqual(add_mock.call_count, 3)
            commit_mock.assert_called_once()

            first_row = add_mock.call_args_list[0][0][0]
            self.assertIsNotNone(first_row.score_snapshot)
            self.assertGreater(first_row.score_snapshot, 0)
            self.assertIsInstance(first_row.breakdown_snapshot, dict)
            self.assertTrue(bool(first_row.breakdown_snapshot))
            self.assertIn("city_detectada", first_row.breakdown_snapshot)
            self.assertEqual(first_row.status, "enviada")

    def test_post_enviar_updates_existing_relation_to_enviada(self):
        ranked = [
            {
                "candidate": _DummyCandidata(101),
                "score": 90,
                "breakdown_snapshot": {"city_detectada": "Santiago"},
                "breakdown": [],
            }
        ]
        existing = _ExistingSolicitudCandidata(solicitud_id=10, candidata_id=101, status="descartada")
        sc_query = _SolicitudCandidataQuery(existing={(10, 101): existing})

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", sc_query), \
                 patch.object(admin_routes.ClienteNotificacion, "query", _ClienteNotificacionQuery()), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes.db.session.add") as add_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"candidata_ids": ["101"]},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 302)
        add_mock.assert_called_once()
        commit_mock.assert_called_once()
        self.assertEqual(existing.status, "enviada")
        self.assertEqual(existing.score_snapshot, 90)
        self.assertEqual(existing.breakdown_snapshot, {"city_detectada": "Santiago"})

    def test_crea_notificacion_al_enviar_candidata(self):
        ranked = [{"candidate": _DummyCandidata(101), "score": 70, "breakdown_snapshot": {"x": "y"}}]
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch.object(admin_routes.ClienteNotificacion, "query", _ClienteNotificacionQuery()), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes.db.session.add") as add_mock, \
                 patch("admin.routes.db.session.commit"):
                resp = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"candidata_ids": ["101"]},
                    follow_redirects=False,
                )
        self.assertEqual(resp.status_code, 302)
        notif_rows = [c[0][0] for c in add_mock.call_args_list if isinstance(c[0][0], admin_routes.ClienteNotificacion)]
        self.assertEqual(len(notif_rows), 1)
        self.assertEqual(notif_rows[0].tipo, "candidatas_enviadas")
        self.assertEqual(notif_rows[0].cliente_id, 7)
        self.assertEqual(notif_rows[0].solicitud_id, 10)
        self.assertEqual(notif_rows[0].payload.get("count"), 1)

    def test_upsert_notificacion_reciente_no_leida(self):
        ranked = [{"candidate": _DummyCandidata(101), "score": 70, "breakdown_snapshot": {"x": "y"}}]
        existing_notif = _DummyNotificacion(payload={"count": 2})

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch.object(admin_routes.ClienteNotificacion, "query", _ClienteNotificacionQuery(existing=existing_notif)), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes.db.session.add") as add_mock, \
                 patch("admin.routes.db.session.commit"):
                resp = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"candidata_ids": ["101"]},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 302)
        notif_rows = [c[0][0] for c in add_mock.call_args_list if isinstance(c[0][0], admin_routes.ClienteNotificacion)]
        self.assertEqual(len(notif_rows), 0)
        self.assertEqual(existing_notif.payload.get("count"), 3)

    def test_post_enviar_with_csrf_token_returns_redirect_not_400(self):
        flask_app.config["WTF_CSRF_ENABLED"] = True

        ranked = [
            {
                "candidate": _DummyCandidata(101),
                "score": 88,
                "operational_score": 80,
                "bonus_test": 8,
                "level": "alta",
                "summary": "ok",
                "risks": [],
                "reasons": [],
                "breakdown": [{"title": "Modalidad", "score": 20, "notes": "ok"}],
                "breakdown_snapshot": {
                    "component_rows": [{"title": "Modalidad", "score": 20, "notes": "ok"}],
                    "city_detectada": "Ciudad detectada: Santiago ✅",
                    "tokens_match": "Tokens coinciden: santiago",
                    "rutas_match": "Rutas: cienfuegos",
                    "modalidad_match": "Modalidad compatible",
                    "horario_match": "Horario compatible",
                    "skills_match": "Funciones compatibles",
                    "mascota_penalty": "Sin penalizacion por mascotas",
                    "test_bonus": "Bonus test: +8",
                },
            }
        ]

        with flask_app.app_context():
            sent_row = _ExistingSolicitudCandidata(solicitud_id=10, candidata_id=101, status="vista")
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery(all_rows=[sent_row])), \
                 patch.object(admin_routes.ClienteNotificacion, "query", _ClienteNotificacionQuery()), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes.db.session.add") as add_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                get_resp = self.client.get("/admin/matching/solicitudes/10")
                self.assertEqual(get_resp.status_code, 200)

                html = get_resp.get_data(as_text=True)
                self.assertIn("Enviadas al cliente", html)
                m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
                self.assertIsNotNone(m, "No se encontró csrf_token en el HTML de matching_detalle")
                csrf_token = m.group(1)

                post_resp = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"csrf_token": csrf_token, "candidata_ids": ["101"]},
                    follow_redirects=False,
                )

            self.assertIn(post_resp.status_code, (302, 303))
            self.assertEqual(add_mock.call_count, 2)
            commit_mock.assert_called_once()

    def test_post_enviar_without_csrf_token_returns_400(self):
        flask_app.config["WTF_CSRF_ENABLED"] = True

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()):
                resp = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"candidata_ids": ["101"]},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 400)

    def test_post_enviar_bloquea_si_activa_en_otro_cliente(self):
        ranked = [{"candidate": _DummyCandidata(101), "score": 88, "breakdown_snapshot": {"x": "y"}}]
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch.object(admin_routes.ClienteNotificacion, "query", _ClienteNotificacionQuery()), \
                 patch("admin.routes._matching_candidate_flags", return_value=({101}, set())), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes.db.session.add") as add_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"candidata_ids": ["101"]},
                    follow_redirects=False,
                )
        self.assertEqual(resp.status_code, 302)
        add_mock.assert_not_called()
        commit_mock.assert_not_called()

    def test_post_enviar_rechazada_previa_requiere_force_send(self):
        ranked = [{"candidate": _DummyCandidata(101), "score": 88, "breakdown_snapshot": {"x": "y"}}]
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery()), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQuery()), \
                 patch.object(admin_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery()), \
                 patch.object(admin_routes.ClienteNotificacion, "query", _ClienteNotificacionQuery()), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), {101})), \
                 patch("admin.routes.rank_candidates", return_value=ranked), \
                 patch("admin.routes.db.session.add") as add_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp_no_force = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"candidata_ids": ["101"]},
                    follow_redirects=False,
                )
                resp_force = self.client.post(
                    "/admin/matching/solicitudes/10/enviar",
                    data={"candidata_ids": ["101"], "force_send": "1"},
                    follow_redirects=False,
                )
        self.assertEqual(resp_no_force.status_code, 302)
        self.assertEqual(resp_force.status_code, 302)
        self.assertEqual(add_mock.call_count, 2)
        self.assertEqual(commit_mock.call_count, 1)

    def test_assignment_sync_marks_others_as_liberada_without_delete(self):
        solicitud = _DummySolicitud()
        row_assigned = _ExistingSolicitudCandidata(solicitud_id=10, candidata_id=101, status="enviada")
        row_other_1 = _ExistingSolicitudCandidata(solicitud_id=10, candidata_id=102, status="vista")
        row_other_2 = _ExistingSolicitudCandidata(solicitud_id=10, candidata_id=103, status="sugerida")
        rows = [row_assigned, row_other_1, row_other_2]

        with flask_app.app_context():
            with patch.object(admin_routes.SolicitudCandidata, "query", _SyncSCQuery(rows)), \
                 patch("admin.routes.db.session.add") as add_mock:
                admin_routes._sync_solicitud_candidatas_after_assignment(solicitud, 101, actor="tester")

        self.assertEqual(len(rows), 3)
        self.assertEqual(row_assigned.status, "seleccionada")
        self.assertEqual(row_other_1.status, "liberada")
        self.assertEqual(row_other_1.breakdown_snapshot.get("client_action"), "liberada_por_asignacion")
        self.assertEqual(row_other_1.breakdown_snapshot.get("assigned_candidata_id"), 101)
        self.assertEqual(row_other_2.status, "liberada")
        add_mock.assert_not_called()

    def test_active_blocking_statuses_exclude_liberada(self):
        self.assertEqual(tuple(admin_routes._ACTIVE_ASSIGNMENT_STATUS), ("enviada", "vista", "seleccionada"))
        self.assertNotIn("liberada", admin_routes._ACTIVE_ASSIGNMENT_STATUS)


if __name__ == "__main__":
    unittest.main()
