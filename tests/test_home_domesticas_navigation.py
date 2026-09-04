# -*- coding: utf-8 -*-

from app import app as flask_app
from models import (
    Candidata,
    CandidataWeb,
    DomainOutbox,
    Entrevista,
    EntrevistaPregunta,
    LlamadaCandidata,
    SeguimientoCandidataCaso,
    Solicitud,
    SolicitudCandidata,
    StaffAuditLog,
)
from tests.t1_testkit import ensure_sqlite_compat_tables


def _login(client, usuario: str, clave: str):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _home_html(usuario: str, clave: str) -> str:
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login(client, usuario, clave).status_code in (302, 303)
    resp = client.get("/home", follow_redirects=False)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _ensure_candidate_tables() -> None:
    with flask_app.app_context():
        ensure_sqlite_compat_tables(
            [
                Candidata,
                Entrevista,
                EntrevistaPregunta,
                LlamadaCandidata,
                SeguimientoCandidataCaso,
                CandidataWeb,
                Solicitud,
                SolicitudCandidata,
                StaffAuditLog,
                DomainOutbox,
            ],
            reset=False,
        )


def test_home_owner_admin_prioriza_domesticas_y_relega_legacy():
    html = _home_html("Owner", "admin123")

    assert "Operación principal" in html
    assert ">Domésticas<" in html
    assert 'href="/admin/candidatas"' in html
    assert 'data-home-candidate-search' in html
    assert 'data-search-url="/admin/candidatas/busqueda-rapida.json"' in html
    assert "Por completar" in html
    assert "Seguimientos pendientes/vencidos" in html
    assert "Listas para trabajar" in html
    assert "Registrar nueva" in html
    assert "Subir Fotos" not in html
    assert "Gestionar Archivos" not in html
    assert "Inscripción legacy" not in html
    assert "Referencias legacy" not in html

    assert "🔍 Buscar / Editar" not in html
    assert "📝 Inscripción" not in html
    assert "🔎 Filtrar Candidatas" not in html
    assert "🚫 Descalificación de Candidatas" not in html
    assert "✅ Finalizar Proceso" not in html
    assert "🗑️ Eliminar Candidata" not in html

    assert "<details class=\"home-admin-tools" in html
    assert "Administración y herramientas" in html
    assert "Finalizar proceso" not in html
    assert "Eliminar candidata" in html
    assert html.index("Domésticas") < html.index("Eliminar candidata")
    assert "Finanzas legacy de candidatas" in html
    assert "Porciento" in html
    assert "Pagos" in html
    assert "Candidatas con porcentaje" in html
    assert "Reclutamiento General" in html
    assert "Vista legacy de procesos" in html


def test_home_secretaria_ve_domesticas_sin_finanzas_nuevas():
    html = _home_html("Karla", "9989")

    assert ">Domésticas<" in html
    assert 'href="/admin/candidatas"' in html
    assert "Buscar solicitudes" not in html
    assert "Registrar nueva" in html
    assert "Subir Fotos" not in html
    assert "Inscripción legacy" not in html
    assert "Referencias legacy" not in html
    assert "Finanzas legacy de candidatas" not in html
    assert "Porciento" not in html
    assert "Pagos" not in html
    assert "Candidatas con porcentaje" not in html


def test_home_domesticas_navegacion_y_rutas_legacy_siguen_respondiendo():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_candidate_tables()
    client = flask_app.test_client()
    assert _login(client, "Owner", "admin123").status_code in (302, 303)

    assert client.get("/admin/candidatas", follow_redirects=False).status_code == 200
    assert client.get("/admin/candidatas/busqueda-rapida.json?q=ana", follow_redirects=False).status_code == 200
    assert client.get("/gestionar_archivos", follow_redirects=False).status_code == 200

    for route in (
        "/buscar",
        "/inscripcion",
        "/filtrar",
        "/porciento",
        "/pagos",
        "/candidatas_porcentaje",
        "/dashboard_procesos",
        "/finalizar_proceso/buscar",
        "/candidatas/eliminar",
    ):
        resp = client.get(route, follow_redirects=False)
        if route.startswith("/finalizar_proceso"):
            assert resp.status_code == 404, route
        else:
            assert resp.status_code == 200, route
