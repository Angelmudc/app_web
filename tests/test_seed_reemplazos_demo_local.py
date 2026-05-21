# -*- coding: utf-8 -*-
from __future__ import annotations

import os

from app import app as flask_app
from config_app import db
from models import Candidata, Cliente, Reemplazo, Solicitud, StaffAuditLog, StaffUser, DomainOutbox, SeguimientoCandidataCaso
from scripts.local import seed_reemplazos_demo as seed_script
from tests.t1_testkit import ensure_sqlite_compat_tables


def _ensure_tables(reset: bool = False) -> None:
    ensure_sqlite_compat_tables(
        [
            StaffUser,
            StaffAuditLog,
            Cliente,
            Candidata,
            Solicitud,
            Reemplazo,
            SeguimientoCandidataCaso,
            DomainOutbox,
        ],
        reset=reset,
    )


def _login_staff(client):
    resp = client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def test_seed_aborta_en_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with flask_app.app_context():
        try:
            seed_script.run_seed(reset=True)
            assert False, "Debe abortar en producción"
        except RuntimeError as exc:
            assert "APP_ENV=production" in str(exc)


def test_seed_reemplazos_demo_flujo_completo_local(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("FLASK_ENV", raising=False)

    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables(reset=True)

        result = seed_script.run_seed(reset=True)
        assert result.created_clientes >= 10
        assert result.created_solicitudes >= 12
        assert result.created_reemplazos >= 12

        activos = Reemplazo.query.filter(Reemplazo.fecha_fin_reemplazo.is_(None)).count()
        cerrados = Reemplazo.query.filter(Reemplazo.fecha_fin_reemplazo.isnot(None)).count()
        cancelados = Reemplazo.query.filter_by(resultado_final="cancelado").count()
        assert activos > 0
        assert cerrados > 0
        assert cancelados > 0

        criticos = [r for r in Reemplazo.query.all() if int(getattr(r, "dias_en_reemplazo", 0) or 0) >= 14]
        assert criticos, "Debe existir al menos un reemplazo de 14+ días"

        cli_hist = Cliente.query.filter(Cliente.codigo.like(f"{seed_script.CLIENT_PREFIX}%")).all()
        assert cli_hist
        multi_found = False
        for c in cli_hist:
            rep_count = (
                db.session.query(Reemplazo.id)
                .join(Solicitud, Solicitud.id == Reemplazo.solicitud_id)
                .filter(Solicitud.cliente_id == int(c.id))
                .count()
            )
            if rep_count >= 2:
                multi_found = True
                break
        assert multi_found, "Debe existir cliente con varios reemplazos"

        before = Reemplazo.query.count()
        second = seed_script.run_seed(reset=False)
        after = Reemplazo.query.count()
        assert second.skipped_existing is True
        assert before == after, "No debe duplicar sin reset"

        seed_script.run_seed(reset=True)
        qa_clientes = Cliente.query.filter(Cliente.codigo.like(f"{seed_script.CLIENT_PREFIX}%")).count()
        qa_reemplazos = (
            db.session.query(Reemplazo.id)
            .join(Solicitud, Solicitud.id == Reemplazo.solicitud_id)
            .join(Cliente, Cliente.id == Solicitud.cliente_id)
            .filter(Cliente.codigo.like(f"{seed_script.CLIENT_PREFIX}%"))
            .count()
        )
        assert qa_clientes >= 10
        assert qa_reemplazos >= 12

    _login_staff(client)

    resp = client.get("/admin/reemplazos", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Panel de reemplazos" in html
    assert "QA-REEMP-" in html

    resp_activos = client.get("/admin/reemplazos?estado=activos", follow_redirects=False)
    assert resp_activos.status_code == 200
    html_activos = resp_activos.get_data(as_text=True)
    assert "Buscando candidata" in html_activos or "Reemplazo activo" in html_activos

    resp_cerrados = client.get("/admin/reemplazos?estado=cerrados", follow_redirects=False)
    assert resp_cerrados.status_code == 200
    html_cerrados = resp_cerrados.get_data(as_text=True)
    assert "Cerrado" in html_cerrados or "Cancelado" in html_cerrados
