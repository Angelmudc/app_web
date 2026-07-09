# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from flask import session

from admin import routes as admin_routes
from app import app as flask_app


def _fake_staff_user(*, role: str = "admin"):
    return SimpleNamespace(
        is_authenticated=True,
        is_active=True,
        role=role,
        email="qa@example.com",
        username="qa_staff",
        id=123,
    )


def test_public_intake_badge_count_uses_request_cache():
    flask_app.config["TESTING"] = True
    fake_user = _fake_staff_user(role="admin")

    with patch("flask_login.utils._get_user", return_value=fake_user):
        with flask_app.test_request_context("/admin/solicitudes", method="GET"):
            session["role"] = "admin"
            with patch("admin.routes._public_intake_badge_count_uncached", return_value=7) as mocked:
                assert admin_routes.public_intake_badge_count() == 7
                assert admin_routes.public_intake_badge_count() == 7
                assert mocked.call_count == 1


def test_tienda_intereses_badge_count_uses_request_cache_for_same_cliente():
    flask_app.config["TESTING"] = True

    with flask_app.test_request_context("/admin/tienda-intereses", method="GET"):
        with patch("admin.routes._tienda_intereses_badge_count_uncached", return_value=3) as mocked:
            assert admin_routes._tienda_intereses_badge_count(cliente_id=55) == 3
            assert admin_routes._tienda_intereses_badge_count(cliente_id=55) == 3
            assert mocked.call_count == 1


def test_seguimiento_badge_and_tables_ready_use_request_cache():
    flask_app.config["TESTING"] = True
    fake_user = _fake_staff_user(role="secretaria")

    with patch("flask_login.utils._get_user", return_value=fake_user):
        with flask_app.test_request_context("/admin/solicitudes", method="GET"):
            session["role"] = "secretaria"
            with patch("admin.routes._seg_tables_ready_uncached", return_value=True) as tables_mock:
                with patch("admin.routes._seguimiento_candidatas_badge_count_uncached", return_value=4) as count_mock:
                    assert admin_routes.seguimiento_candidatas_badge_count() == 4
                    assert admin_routes.seguimiento_candidatas_badge_count() == 4
                    assert tables_mock.call_count == 1
                    assert count_mock.call_count == 1


def test_seg_tables_ready_caches_positive_result_across_non_testing_requests():
    previous_testing = flask_app.config.get("TESTING")
    flask_app.config["TESTING"] = False
    admin_routes._SEG_TABLES_READY_PROCESS_CACHE = None

    try:
        with flask_app.test_request_context("/admin/solicitudes", method="GET"):
            with patch("admin.routes._seg_tables_ready_uncached", return_value=True) as mocked:
                assert admin_routes._seg_tables_ready() is True
                assert mocked.call_count == 1

        with flask_app.test_request_context("/admin/clientes", method="GET"):
            with patch("admin.routes._seg_tables_ready_uncached", return_value=True) as mocked:
                assert admin_routes._seg_tables_ready() is True
                assert mocked.call_count == 0
    finally:
        admin_routes._SEG_TABLES_READY_PROCESS_CACHE = None
        flask_app.config["TESTING"] = previous_testing


def test_candidatas_por_finalizar_badge_count_uses_request_cache_even_without_global_cache():
    flask_app.config["TESTING"] = True
    fake_user = _fake_staff_user(role="admin")

    with patch("flask_login.utils._get_user", return_value=fake_user):
        with flask_app.test_request_context("/admin/solicitudes", method="GET"):
            session["role"] = "admin"
            with patch("admin.routes._candidatas_finalizar_badge_cache_get", return_value=None):
                with patch("admin.routes._candidatas_finalizar_badge_cache_set"):
                    with patch("admin.routes._build_candidatas_por_finalizar_rows", return_value=6) as mocked:
                        assert admin_routes.candidatas_por_finalizar_badge_count() == 6
                        assert admin_routes.candidatas_por_finalizar_badge_count() == 6
                        assert mocked.call_count == 1
