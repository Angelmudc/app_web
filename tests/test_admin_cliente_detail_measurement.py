# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import secrets
from datetime import datetime

from app import app as flask_app
from config_app import db
from models import Candidata, Cliente, ContratoDigital, Entrevista, PagoSolicitud, Reemplazo, Solicitud, StaffUser, TareaCliente
from tests.t1_testkit import ensure_sqlite_compat_tables


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables(
        [
            StaffUser,
            Candidata,
            Entrevista,
            Cliente,
            Solicitud,
            PagoSolicitud,
            Reemplazo,
            TareaCliente,
            ContratoDigital,
        ],
        reset=True,
    )


def _login_admin(client) -> None:
    resp = client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _seed_cliente() -> int:
    token = secrets.token_hex(4)
    cliente = Cliente(
        codigo=f"MEASURE-{token}",
        nombre_completo=f"Cliente Measure {token}",
        email=f"measure_{token}@example.com",
        telefono=f"809{int(token[:6], 16) % 10**7:07d}",
        fecha_registro=datetime(2026, 1, 1, 12, 0, 0),
    )
    db.session.add(cliente)
    db.session.commit()
    return int(cliente.id)


def _seed_solicitud(cliente_id: int) -> int:
    solicitud = Solicitud(
        cliente_id=int(cliente_id),
        codigo_solicitud=f"SOL-M-{secrets.token_hex(4)}",
        estado="activa",
        tipo_plan="basico",
        fecha_solicitud=datetime(2026, 1, 2, 10, 0, 0),
        ciudad_sector="Santo Domingo",
    )
    db.session.add(solicitud)
    db.session.commit()
    return int(solicitud.id)


def _seed_tarea(cliente_id: int) -> None:
    tarea = TareaCliente(
        cliente_id=int(cliente_id),
        titulo="Llamar cliente",
        estado="pendiente",
    )
    db.session.add(tarea)
    db.session.commit()


def test_cliente_detail_measurement_headers_opt_in():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["APP_ENV"] = "test"
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
        cliente_id = _seed_cliente()
        _seed_solicitud(cliente_id)
        _seed_tarea(cliente_id)
    _login_admin(client)
    resp = client.get(
        f"/admin/clientes/{cliente_id}",
        headers={"X-Admin-Cliente-Detail-Measure": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "X-P1C1-Perf-DB-Queries" in resp.headers
    assert "X-Admin-Cliente-Detail-Metrics" in resp.headers
    payload = resp.headers["X-Admin-Cliente-Detail-Metrics"]
    assert '"tables"' in payload
    assert '"lazy_loads"' in payload
    assert '"blocks"' in payload


def test_cliente_detail_measurement_header_no_sale_sin_opt_in():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["APP_ENV"] = "test"
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
        cliente_id = _seed_cliente()
        _seed_solicitud(cliente_id)
    _login_admin(client)
    resp = client.get(f"/admin/clientes/{cliente_id}", follow_redirects=False)
    assert resp.status_code == 200
    assert "X-P1C1-Perf-DB-Queries" in resp.headers
    assert "X-Admin-Cliente-Detail-Metrics" not in resp.headers
