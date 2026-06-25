# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import secrets
from datetime import datetime
from decimal import Decimal

from app import app as flask_app
from config_app import db
import admin.routes as admin_routes
from models import Cliente, PagoSolicitud, Solicitud, StaffUser
from services.payment_ledger import calcular_total_pagado_cliente
from tests.t1_testkit import ensure_sqlite_compat_tables


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables(
        [
            StaffUser,
            Cliente,
            Solicitud,
            PagoSolicitud,
        ],
        reset=True,
    )


def _login_admin(client):
    resp = client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _seed_cliente() -> int:
    token = secrets.token_hex(6)
    cliente = Cliente(
        codigo=f"KPI-{token}",
        nombre_completo=f"Cliente KPI {token}",
        email=f"kpi_{token}@example.com",
        telefono=f"809{int(token[:6], 16) % 10**7:07d}",
    )
    db.session.add(cliente)
    db.session.commit()
    return int(cliente.id)


def _seed_solicitud(cliente_id: int, *, estado: str = "activa", monto_pagado: str = "0.00") -> int:
    token = secrets.token_hex(5)
    s = Solicitud(
        cliente_id=int(cliente_id),
        codigo_solicitud=f"SOL-KPI-{token}",
        estado=estado,
        tipo_plan="basico",
        monto_pagado=monto_pagado,
        fecha_solicitud=datetime(2026, 1, 1, 12, 0, 0),
    )
    db.session.add(s)
    db.session.commit()
    return int(s.id)


def _add_mov(cliente_id: int, solicitud_id: int, *, monto: str, tipo: str, anulado: bool = False, origen_id: str = "") -> None:
    mov = PagoSolicitud(
        cliente_id=int(cliente_id),
        solicitud_id=int(solicitud_id),
        monto=monto,
        tipo_pago=tipo,
        origen="test",
        origen_id=origen_id or f"mov:{secrets.token_hex(4)}",
        anulado_at=(datetime(2026, 1, 15, 10, 0, 0) if anulado else None),
    )
    db.session.add(mov)
    db.session.commit()


def test_1_total_pagado_cliente_suma_todos_los_movimientos():
    with flask_app.app_context():
        _ensure_tables()
        cliente_id = _seed_cliente()
        s1 = _seed_solicitud(cliente_id, estado="pagada")
        s2 = _seed_solicitud(cliente_id, estado="activa")
        _add_mov(cliente_id, s1, monto="2500.00", tipo="abono")
        _add_mov(cliente_id, s1, monto="2500.00", tipo="pago")
        _add_mov(cliente_id, s2, monto="1750.00", tipo="abono")
        total = calcular_total_pagado_cliente(cliente_id)
        assert str(total) == "6750.00"


def test_2_total_pagado_cliente_cuenta_multiples_pagos_misma_solicitud_reactivada():
    with flask_app.app_context():
        _ensure_tables()
        cliente_id = _seed_cliente()
        s1 = _seed_solicitud(cliente_id, estado="activa")
        _add_mov(cliente_id, s1, monto="1000.00", tipo="abono")
        _add_mov(cliente_id, s1, monto="1200.00", tipo="pago")
        _add_mov(cliente_id, s1, monto="1300.00", tipo="pago")
        total = calcular_total_pagado_cliente(cliente_id)
        assert str(total) == "3500.00"


def test_3_total_pagado_cliente_excluye_movimientos_anulados():
    with flask_app.app_context():
        _ensure_tables()
        cliente_id = _seed_cliente()
        s1 = _seed_solicitud(cliente_id)
        _add_mov(cliente_id, s1, monto="2000.00", tipo="pago")
        _add_mov(cliente_id, s1, monto="900.00", tipo="pago", anulado=True)
        total = calcular_total_pagado_cliente(cliente_id)
        assert str(total) == "2000.00"


def test_4_total_pagado_cliente_devolucion_resta():
    with flask_app.app_context():
        _ensure_tables()
        cliente_id = _seed_cliente()
        s1 = _seed_solicitud(cliente_id)
        _add_mov(cliente_id, s1, monto="3000.00", tipo="pago")
        _add_mov(cliente_id, s1, monto="500.00", tipo="devolucion")
        total = calcular_total_pagado_cliente(cliente_id)
        assert str(total) == "2500.00"


def test_5_total_pagado_cliente_no_depende_del_estado_de_solicitud():
    with flask_app.app_context():
        _ensure_tables()
        cliente_id = _seed_cliente()
        s_activa = _seed_solicitud(cliente_id, estado="activa")
        s_pagada = _seed_solicitud(cliente_id, estado="pagada")
        s_reemp = _seed_solicitud(cliente_id, estado="reemplazo")
        s_espera = _seed_solicitud(cliente_id, estado="espera_pago")
        _add_mov(cliente_id, s_activa, monto="500.00", tipo="abono")
        _add_mov(cliente_id, s_pagada, monto="1500.00", tipo="pago")
        _add_mov(cliente_id, s_reemp, monto="700.00", tipo="pago")
        _add_mov(cliente_id, s_espera, monto="300.00", tipo="abono")
        total = calcular_total_pagado_cliente(cliente_id)
        assert str(total) == "3000.00"


def test_6_kpi_no_usa_monto_pagado_legacy_si_hay_ledger_evita_doble_conteo():
    with flask_app.app_context():
        _ensure_tables()
        cliente_id = _seed_cliente()
        s1 = _seed_solicitud(cliente_id, estado="pagada", monto_pagado="3500.00")
        _add_mov(cliente_id, s1, monto="2500.00", tipo="abono")
        _add_mov(cliente_id, s1, monto="1000.00", tipo="pago")
        cliente = Cliente.query.get(cliente_id)
        solicitudes = Solicitud.query.filter_by(cliente_id=cliente_id).all()
        kpi = admin_routes._build_cliente_summary_kpi(cliente=cliente, solicitudes=solicitudes)
        assert str(kpi["monto_total_pagado"]) == "3500.00"


def test_7_detalle_cliente_summary_renderiza_total_historico_correcto():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
        cliente_id = _seed_cliente()
        s1 = _seed_solicitud(cliente_id, estado="pagada")
        s2 = _seed_solicitud(cliente_id, estado="activa")
        _add_mov(cliente_id, s1, monto="2500.00", tipo="abono")
        _add_mov(cliente_id, s1, monto="2500.00", tipo="pago")
        _add_mov(cliente_id, s2, monto="1750.00", tipo="abono")
    _login_admin(client)
    resp = client.get(f"/admin/clientes/{cliente_id}/_summary", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Resumen operativo" in html
    assert "Información General" not in html
    assert "Monto total pagado" in html
    assert "RD$ 6,750.00" in html
