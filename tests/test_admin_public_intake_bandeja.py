# -*- coding: utf-8 -*-

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
from utils.timezone import utc_now_naive


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

    fake_page_new = SimpleNamespace(
        items=[
            SimpleNamespace(
                id=99,
                cliente_id=7,
                codigo_solicitud="CL-PUB-01-A",
                fecha_solicitud=None,
                review_status="nuevo",
                public_form_source="cliente_nuevo",
                cliente=SimpleNamespace(id=7, codigo="EXT-0007", nombre_completo="Cliente Test", telefono="8090000000", email="x@test.com"),
            )
        ],
        page=1,
        pages=1,
        has_prev=False,
        has_next=False,
        prev_num=1,
        next_num=1,
    )
    fake_page_today = SimpleNamespace(
        items=[
            SimpleNamespace(
                id=100,
                cliente_id=7,
                codigo_solicitud="CL-PUB-01-B",
                fecha_solicitud=None,
                review_status="revisado",
                public_form_source="cliente_existente",
                cliente=SimpleNamespace(id=8, codigo="EXT-0008", nombre_completo="Cliente Test 2", telefono="8091111111", email="y@test.com"),
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
        def __init__(self):
            self._paginate_calls = 0

        def options(self, *_a, **_k):
            return self

        def filter(self, *_a, **_k):
            return self

        def order_by(self, *_a, **_k):
            return self

        def paginate(self, **_kwargs):
            self._paginate_calls += 1
            if self._paginate_calls == 1:
                return fake_page_new
            return fake_page_today

        def count(self):
            return 1

    with flask_app.app_context():
        with patch("admin.routes.Solicitud.query", _FakeQuery()):
            inbox = client.get("/admin/solicitudes/publicas/nuevas", follow_redirects=False)

    assert inbox.status_code == 200
    inbox_html = inbox.get_data(as_text=True)
    assert "Bandeja de formularios públicos" in inbox_html
    assert "Solicitudes nuevas por revisar" in inbox_html
    assert "Solicitudes de hoy" in inbox_html
    assert "Cliente Test" in inbox_html
    assert "EXT-0007" in inbox_html
    assert "8090000000" in inbox_html
    assert "x@test.com" in inbox_html
    assert "Cliente Test 2" in inbox_html
    assert "Ver cliente" in inbox_html
    assert "/admin/clientes/7" in inbox_html
    assert "Revisar" not in inbox_html


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


def test_clientes_list_shows_inbox_quick_card_only_for_owner_admin():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    owner = flask_app.test_client()
    assert _login(owner, "Owner", "admin123").status_code in (302, 303)
    admin = flask_app.test_client()
    assert _login(admin, "Cruz", "8998").status_code in (302, 303)
    secretaria = flask_app.test_client()
    assert _login(secretaria, "Karla", "9989").status_code in (302, 303)

    with patch("admin.routes._public_intake_badge_count", return_value=3):
        owner_html = owner.get("/admin/clientes", follow_redirects=False).get_data(as_text=True)
    assert "Bandeja de solicitudes nuevas" in owner_html
    assert "3 nuevas" in owner_html

    with patch("admin.routes._public_intake_badge_count", return_value=2):
        admin_html = admin.get("/admin/clientes", follow_redirects=False).get_data(as_text=True)
    assert "Bandeja de solicitudes nuevas" in admin_html
    assert "2 nuevas" in admin_html

    secretaria_html = secretaria.get("/admin/clientes", follow_redirects=False).get_data(as_text=True)
    assert "Bandeja de solicitudes nuevas" not in secretaria_html


def test_bandeja_nuevas_only_nuevo_and_hoy_shows_all_statuses_with_real_fixtures():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    owner = flask_app.test_client()
    admin = flask_app.test_client()
    secretaria = flask_app.test_client()

    assert _login(owner, "Owner", "admin123").status_code in (302, 303)
    assert _login(admin, "Cruz", "8998").status_code in (302, 303)
    assert _login(secretaria, "Karla", "9989").status_code in (302, 303)

    suffix = uuid.uuid4().hex[:8]
    s_new_code = f"SOL-PUB-N-{suffix}"
    s_mgmt_code = f"SOL-PUB-G-{suffix}"
    s_rev_code = f"SOL-PUB-R-{suffix}"
    s_dis_code = f"SOL-PUB-D-{suffix}"
    n_new = f"Cliente Nuevo {suffix}"
    n_mgmt = f"Cliente Gestion {suffix}"
    n_rev = f"Cliente Revisado {suffix}"
    n_dis = f"Cliente Descartado {suffix}"
    now = utc_now_naive()

    rows = [
        SimpleNamespace(
            id=1,
            cliente_id=11,
            codigo_solicitud=s_new_code,
            fecha_solicitud=now,
            public_form_source="cliente_nuevo",
            review_status="nuevo",
            cliente=SimpleNamespace(id=11, codigo="EXT-0011", nombre_completo=n_new, telefono="8090000001", email="n@test.com"),
        ),
        SimpleNamespace(
            id=2,
            cliente_id=11,
            codigo_solicitud=s_mgmt_code,
            fecha_solicitud=now - timedelta(minutes=3),
            public_form_source="cliente_existente",
            review_status="en_gestion",
            cliente=SimpleNamespace(id=11, codigo="EXT-0011", nombre_completo=n_mgmt, telefono="8090000002", email="g@test.com"),
        ),
        SimpleNamespace(
            id=3,
            cliente_id=11,
            codigo_solicitud=s_rev_code,
            fecha_solicitud=now - timedelta(minutes=6),
            public_form_source="solicitud_publica",
            review_status="revisado",
            cliente=SimpleNamespace(id=11, codigo="EXT-0011", nombre_completo=n_rev, telefono="8090000003", email="r@test.com"),
        ),
        SimpleNamespace(
            id=4,
            cliente_id=11,
            codigo_solicitud=s_dis_code,
            fecha_solicitud=now - timedelta(minutes=9),
            public_form_source="solicitud_publica",
            review_status="descartado",
            cliente=SimpleNamespace(id=11, codigo="EXT-0011", nombre_completo=n_dis, telefono="8090000004", email="d@test.com"),
        ),
    ]

    class _FakeQuery:
        def __init__(self, items):
            self._items = list(items)
            self._paginate_calls = 0

        def options(self, *_a, **_k):
            return self

        def filter(self, *args, **_kwargs):
            return self

        def order_by(self, *_a, **_k):
            return self

        def paginate(self, **_kwargs):
            self._paginate_calls += 1
            selected = [r for r in self._items if r.review_status == "nuevo"] if self._paginate_calls == 1 else list(self._items)
            return SimpleNamespace(
                items=selected,
                page=1,
                pages=1,
                has_prev=False,
                has_next=False,
                prev_num=1,
                next_num=1,
            )

        def count(self):
            return len(self._items)

    with flask_app.app_context():
        with patch("admin.routes.Solicitud.query", _FakeQuery(rows)):
            owner_resp = owner.get("/admin/solicitudes/publicas/nuevas?per_page=50", follow_redirects=False)
    with flask_app.app_context():
        with patch("admin.routes.Solicitud.query", _FakeQuery(rows)):
            admin_resp = admin.get("/admin/solicitudes/publicas/nuevas?per_page=50", follow_redirects=False)
    denied_resp = secretaria.get("/admin/solicitudes/publicas/nuevas", follow_redirects=False)

    assert owner_resp.status_code == 200
    assert admin_resp.status_code == 200
    assert denied_resp.status_code == 403

    html = owner_resp.get_data(as_text=True)
    assert "Solicitudes nuevas por revisar" in html
    assert "Solicitudes de hoy" in html

    nuevas_section = html.split("Solicitudes nuevas por revisar", 1)[1].split("Solicitudes de hoy", 1)[0]
    hoy_section = html.split("Solicitudes de hoy", 1)[1]

    # Bandeja nuevas: solo review_status = nuevo
    assert n_new in nuevas_section
    assert "EXT-0011" in nuevas_section
    assert n_mgmt not in nuevas_section
    assert n_rev not in nuevas_section
    assert n_dis not in nuevas_section

    # Solicitudes de hoy: incluye todos los estados del día
    assert n_new in hoy_section
    assert n_mgmt in hoy_section
    assert n_rev in hoy_section
    assert n_dis in hoy_section


def test_bandeja_shows_fallback_and_disables_ver_cliente_when_cliente_missing():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    fake_page_new = SimpleNamespace(
        items=[
            SimpleNamespace(
                id=301,
                cliente_id=None,
                codigo_solicitud="CL-PUB-Z",
                fecha_solicitud=None,
                review_status="nuevo",
                public_form_source="solicitud_publica",
                cliente=None,
            )
        ],
        page=1,
        pages=1,
        has_prev=False,
        has_next=False,
        prev_num=1,
        next_num=1,
    )
    fake_page_today = SimpleNamespace(
        items=[],
        page=1,
        pages=1,
        has_prev=False,
        has_next=False,
        prev_num=1,
        next_num=1,
    )

    class _FakeQuery:
        def __init__(self):
            self._paginate_calls = 0

        def options(self, *_a, **_k):
            return self

        def filter(self, *_a, **_k):
            return self

        def order_by(self, *_a, **_k):
            return self

        def paginate(self, **_kwargs):
            self._paginate_calls += 1
            return fake_page_new if self._paginate_calls == 1 else fake_page_today

        def count(self):
            return 0

    with flask_app.app_context():
        with patch("admin.routes.Solicitud.query", _FakeQuery()):
            resp = client.get("/admin/solicitudes/publicas/nuevas", follow_redirects=False)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Cliente no disponible" in html
    assert "Ver cliente" in html
    assert "disabled" in html
