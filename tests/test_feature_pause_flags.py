# -*- coding: utf-8 -*-

from app import app as flask_app
from models import (
    Candidata,
    CandidataWeb,
    DomainOutbox,
    Entrevista,
    EntrevistaPregunta,
    LlamadaCandidata,
    Reemplazo,
    SeguimientoCandidataCaso,
    Solicitud,
    SolicitudCandidata,
    StaffAuditLog,
)
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.feature_flags import feature_enabled


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _ensure_home_tables() -> None:
    with flask_app.app_context():
        ensure_sqlite_compat_tables(
            [
                Candidata,
                CandidataWeb,
                DomainOutbox,
                Entrevista,
                EntrevistaPregunta,
                LlamadaCandidata,
                Reemplazo,
                SeguimientoCandidataCaso,
                Solicitud,
                SolicitudCandidata,
                StaffAuditLog,
            ],
            reset=False,
        )


def test_feature_flags_default_paused_routes_and_navigation():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_home_tables()

    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    home = client.get("/home", follow_redirects=False)
    assert home.status_code == 200
    html = home.get_data(as_text=True)

    assert "Finalizar proceso" not in html
    assert "Buscar otra" not in html
    assert "Test de compatibilidad" not in html
    assert "Decisiones" not in html
    assert "Perfiles públicos" not in html

    assert client.get("/finalizar_proceso/buscar", follow_redirects=False).status_code == 404
    assert client.get("/candidatas/llamadas", follow_redirects=False).status_code == 404
    assert client.get("/secretarias/compat/candidata", follow_redirects=False).status_code == 404
    assert client.get("/admin/matching/inteligente", follow_redirects=False).status_code == 404
    assert client.get("/admin/candidatas-web", follow_redirects=False).status_code == 404


def test_feature_flags_default_paused_candidatas_dashboard_and_reenable():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_home_tables()

    client = flask_app.test_client()
    assert _login(client, "Karla", "9989").status_code in (302, 303)

    home = client.get("/admin/candidatas", follow_redirects=False)
    assert home.status_code == 200
    html = home.get_data(as_text=True)
    assert "cand-attention-kpis" not in html
    assert "candPriorityCriteria" not in html
    assert "queue_counts" not in html

    old_flags = dict(flask_app.config.get("FEATURE_FLAGS") or {})
    try:
        new_flags = dict(old_flags)
        new_flags["candidatas_dashboard"] = True
        flask_app.config["FEATURE_FLAGS"] = new_flags
        flask_app.config["FEATURE_CANDIDATAS_DASHBOARD"] = True

        with flask_app.app_context():
            assert feature_enabled("candidatas_dashboard") is True

        client_on = flask_app.test_client()
        assert _login(client_on, "Karla", "9989").status_code in (302, 303)
        on_resp = client_on.get("/admin/candidatas", follow_redirects=False)
        assert on_resp.status_code == 200
        on_html = on_resp.get_data(as_text=True)
        assert "cand-attention-kpis" in on_html
        assert "candPriorityCriteria" in on_html
    finally:
        flask_app.config["FEATURE_FLAGS"] = old_flags
        flask_app.config["FEATURE_CANDIDATAS_DASHBOARD"] = bool(old_flags.get("candidatas_dashboard", False))


def test_feature_flag_can_reenable_finalizar_proceso_temporarily():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_home_tables()

    old_flags = dict(flask_app.config.get("FEATURE_FLAGS") or {})
    try:
        new_flags = dict(old_flags)
        new_flags["finalizar_proceso"] = True
        flask_app.config["FEATURE_FLAGS"] = new_flags
        flask_app.config["FEATURE_FINALIZAR_PROCESO"] = True

        with flask_app.app_context():
            assert feature_enabled("finalizar_proceso") is True

        client = flask_app.test_client()
        assert _login(client, "Owner", "admin123").status_code in (302, 303)

        home = client.get("/home", follow_redirects=False)
        assert home.status_code == 200
        html = home.get_data(as_text=True)
        assert "Finalizar proceso" in html
        assert client.get("/finalizar_proceso/buscar", follow_redirects=False).status_code == 200
    finally:
        flask_app.config["FEATURE_FLAGS"] = old_flags
        flask_app.config["FEATURE_FINALIZAR_PROCESO"] = bool(old_flags.get("finalizar_proceso", False))
