# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def test_owner_sees_public_intake_menu_and_bandeja_lists_pending_items():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    home = client.get("/home", follow_redirects=False)
    assert home.status_code == 200
    html = home.get_data(as_text=True)
    assert "Formularios nuevos" in html

    fake_page = SimpleNamespace(
        items=[
            SimpleNamespace(
                id=99,
                cliente_id=7,
                codigo_solicitud="CL-PUB-01-A",
                fecha_solicitud=None,
                review_status="nuevo",
                public_form_source="cliente_nuevo",
                cliente=SimpleNamespace(nombre_completo="Cliente Test", telefono="8090000000", email="x@test.com"),
            )
        ],
        page=1,
        pages=1,
        has_prev=False,
        has_next=False,
        prev_num=1,
        next_num=1,
    )

    class _FakeQuery:
        def options(self, *_a, **_k):
            return self

        def filter(self, *_a, **_k):
            return self

        def order_by(self, *_a, **_k):
            return self

        def paginate(self, **_kwargs):
            return fake_page

        def count(self):
            return 1

    with flask_app.app_context():
        with patch("admin.routes.Solicitud.query", _FakeQuery()):
            inbox = client.get("/admin/solicitudes/publicas/nuevas", follow_redirects=False)

    assert inbox.status_code == 200
    inbox_html = inbox.get_data(as_text=True)
    assert "Bandeja de formularios públicos" in inbox_html
    assert "Cliente Test" in inbox_html
    assert "Cliente nuevo" in inbox_html


def test_mark_reviewed_removes_item_from_pending_badge_count():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    with patch("admin.routes._public_intake_badge_count", side_effect=[1, 0]):
        before = client.get("/admin/solicitudes/publicas/nuevas/badge.json", follow_redirects=False)
        assert before.status_code == 200
        assert int((before.get_json() or {}).get("count") or 0) == 1

        fake_s = SimpleNamespace(id=77, review_status="nuevo", reviewed_by=None, reviewed_at=None)
        with flask_app.app_context():
            with patch("admin.routes.Solicitud.query", SimpleNamespace(get_or_404=lambda _sid: fake_s)), \
                 patch("admin.routes.db.session.commit", return_value=None):
                resp = client.post(
                    "/admin/solicitudes/77/review-status",
                    data={"review_status": "revisado", "next": "/admin/solicitudes/publicas/nuevas"},
                    follow_redirects=False,
                )

        assert resp.status_code in (302, 303)

        after = client.get("/admin/solicitudes/publicas/nuevas/badge.json", follow_redirects=False)
        assert after.status_code == 200
        assert int((after.get_json() or {}).get("count") or 0) == 0


def test_secretaria_cannot_access_public_intake_bandeja():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client, "Karla", "9989").status_code in (302, 303)

    home = client.get("/home", follow_redirects=False)
    assert home.status_code == 200
    assert "Formularios nuevos" not in home.get_data(as_text=True)

    denied = client.get("/admin/solicitudes/publicas/nuevas", follow_redirects=False)
    assert denied.status_code == 403
