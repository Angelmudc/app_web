# -*- coding: utf-8 -*-

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from werkzeug.exceptions import NotFound
from flask import get_flashed_messages

from app import app as flask_app
import clientes.routes as clientes_routes


class _DummySolicitud:
    def __init__(self, solicitud_id: int, codigo: str):
        self.id = solicitud_id
        self.codigo_solicitud = codigo
        self.candidata_id = None
        self.candidata = None


class _DummyNotif:
    def __init__(self, notif_id: int, cliente_id: int, solicitud_id: int, is_read=False, is_deleted=False):
        self.id = notif_id
        self.cliente_id = cliente_id
        self.solicitud_id = solicitud_id
        self.tipo = "candidatas_enviadas"
        self.titulo = "Candidatas enviadas"
        self.cuerpo = "La agencia te envio candidatas compatibles."
        self.payload = {"count": 2}
        self.is_read = is_read
        self.is_deleted = is_deleted
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.solicitud = _DummySolicitud(solicitud_id, f"SOL-{solicitud_id:03d}")


class _DummyCandidate:
    def __init__(self):
        self.fila = 501
        self.codigo = "C-501"
        self.nombre_completo = "Ana Perez"
        self.edad = "30"
        self.modalidad_trabajo_preferida = "salida diaria"


class _DummySC:
    def __init__(self):
        self.id = 91
        self.solicitud_id = 10
        self.candidata_id = 501
        self.status = "enviada"
        self.score_snapshot = 88
        self.breakdown_snapshot = {}
        self.candidata = _DummyCandidate()


class _NotifQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = {}

    def filter_by(self, **kwargs):
        self.filters.update(kwargs)
        return self

    def filter(self, *args, **kwargs):
        return self

    def join(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, n):
        self._limit = int(n or 0)
        return self

    def all(self):
        items = self._items()
        lim = getattr(self, "_limit", 0)
        if lim > 0:
            return items[:lim]
        return items

    def count(self):
        return len(self._items())

    def _items(self):
        out = []
        for row in self.rows:
            ok = True
            for k, v in self.filters.items():
                if getattr(row, k, None) != v:
                    ok = False
                    break
            if ok:
                out.append(row)
        return out

    def paginate(self, page=1, per_page=20, error_out=False):
        items = self._items()
        start = (page - 1) * per_page
        end = start + per_page
        paged = items[start:end]
        pages = max(1, (len(items) + per_page - 1) // per_page)
        return SimpleNamespace(
            items=paged,
            page=page,
            pages=pages,
            total=len(items),
            has_prev=page > 1,
            has_next=page < pages,
            prev_num=page - 1,
            next_num=page + 1,
        )

    def first_or_404(self):
        items = self._items()
        if not items:
            raise NotFound()
        return items[0]


class ClienteNotificacionesFlowTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False

    def test_notificaciones_html_redirects_to_dashboard(self):
        fake_user = SimpleNamespace(id=7, nombre_completo="Cliente Demo")
        target = clientes_routes.notificaciones_list
        for _ in range(2):
            target = target.__wrapped__
        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", fake_user):
                with flask_app.test_request_context("/clientes/notificaciones", method="GET"):
                    resp = target()
                    flashed = get_flashed_messages(with_categories=True)
        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/clientes/dashboard", resp.location)
        self.assertTrue(any("retirada" in str(msg).lower() for _cat, msg in flashed))

    def test_cliente_ownership_on_actions(self):
        fake_user = SimpleNamespace(id=7, nombre_completo="Cliente Demo")
        other = _DummyNotif(notif_id=2, cliente_id=99, solicitud_id=11)

        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes.ClienteNotificacion, "query", _NotifQuery([other])):
                with flask_app.test_request_context("/clientes/notificaciones/2/ver", method="POST"):
                    with self.assertRaises(NotFound):
                        clientes_routes.notificacion_ver.__wrapped__.__wrapped__(2)

    def test_notificaciones_json_unread_count_and_items(self):
        fake_user = SimpleNamespace(id=7, nombre_completo="Cliente Demo")
        own_1 = _DummyNotif(notif_id=11, cliente_id=7, solicitud_id=10, is_read=False)
        own_2 = _DummyNotif(notif_id=12, cliente_id=7, solicitud_id=10, is_read=True)
        other = _DummyNotif(notif_id=13, cliente_id=99, solicitud_id=11, is_read=False)

        target = clientes_routes.notificaciones_json
        for _ in range(2):
            target = target.__wrapped__

        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes.ClienteNotificacion, "query", _NotifQuery([own_1, own_2, other])):
                with flask_app.test_request_context("/clientes/notificaciones.json?limit=10", method="GET"):
                    resp = target()

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["unread_count"], 1)
        self.assertEqual(len(data["items"]), 2)
        self.assertTrue(all("/clientes/solicitudes/10/candidatas" in x["url"] for x in data["items"]))

    def test_ver_notificacion_marca_leida_y_redirige(self):
        fake_user = SimpleNamespace(id=7, nombre_completo="Cliente Demo")
        notif = _DummyNotif(notif_id=3, cliente_id=7, solicitud_id=10, is_read=False)
        target = clientes_routes.notificacion_ver
        for _ in range(2):
            target = target.__wrapped__

        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes.ClienteNotificacion, "query", _NotifQuery([notif])), \
                 patch("clientes.routes.db.session.commit") as commit_mock:
                with flask_app.test_request_context("/clientes/notificaciones/3/ver", method="POST", data={"csrf_token": "ok"}):
                    resp = target(3)

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.location.endswith("/clientes/solicitudes/10/candidatas#candidatas-enviadas"))
        self.assertTrue(notif.is_read)
        commit_mock.assert_called_once()

    def test_eliminar_notificacion_soft_delete(self):
        fake_user = SimpleNamespace(id=7, nombre_completo="Cliente Demo")
        notif = _DummyNotif(notif_id=4, cliente_id=7, solicitud_id=10, is_deleted=False)
        target = clientes_routes.notificacion_eliminar
        for _ in range(2):
            target = target.__wrapped__

        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes.ClienteNotificacion, "query", _NotifQuery([notif])), \
                 patch("clientes.routes.db.session.commit") as commit_mock:
                with flask_app.test_request_context("/clientes/notificaciones/4/eliminar", method="POST", data={"csrf_token": "ok"}):
                    resp = target(4)

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.location.endswith("/clientes/notificaciones"))
        self.assertTrue(notif.is_deleted)
        commit_mock.assert_called_once()

    def test_marcar_leida_json_responde_ok_y_unread_count(self):
        fake_user = SimpleNamespace(id=7, nombre_completo="Cliente Demo")
        notif = _DummyNotif(notif_id=5, cliente_id=7, solicitud_id=10, is_read=False)
        target = clientes_routes.notificacion_marcar_leida
        for _ in range(2):
            target = target.__wrapped__

        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes.ClienteNotificacion, "query", _NotifQuery([notif])), \
                 patch("clientes.routes.db.session.commit") as commit_mock:
                with flask_app.test_request_context(
                    "/clientes/notificaciones/5/marcar-leida",
                    method="POST",
                    data={"csrf_token": "ok"},
                    headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                ):
                    resp = target(5)

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("ok"), True)
        self.assertEqual(data.get("marked_id"), 5)
        self.assertEqual(data.get("unread_count"), 0)
        self.assertTrue(notif.is_read)
        commit_mock.assert_called_once()

    def test_solicitud_candidatas_template_includes_csrf_meta_and_notifications_js(self):
        fake_user = SimpleNamespace(id=7, nombre_completo="Cliente Demo")
        solicitud = _DummySolicitud(10, "SOL-010")
        sc = _DummySC()

        target = clientes_routes.solicitud_candidatas
        for _ in range(2):
            target = target.__wrapped__

        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes, "_get_solicitud_cliente_or_404", return_value=solicitud), \
                 patch.object(clientes_routes.SolicitudCandidata, "query", _NotifQuery([sc])):
                with flask_app.test_request_context("/clientes/solicitudes/10/candidatas", method="GET"):
                    html = target(10)

        self.assertIn('meta name="csrf-token"', html)
        self.assertIn("client_notifications.js", html)


if __name__ == "__main__":
    unittest.main()
