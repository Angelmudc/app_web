# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app import app as flask_app
import clientes.routes as clientes_routes
from config_app import db
from models import Cliente, PublicSolicitudClienteNuevoTokenUso, PublicSolicitudTokenUso, Solicitud
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.timezone import utc_now_naive
from werkzeug.datastructures import MultiDict


def _unwrap_view(fn, wraps: int):
    out = fn
    for _ in range(wraps):
        out = out.__wrapped__
    return out


def _seed_cliente_y_solicitud(*, codigo: str, solicitud_codigo: str, tipo_plan=None):
    cliente = Cliente(
        codigo=codigo,
        nombre_completo="Cliente Demo",
        email=f"{codigo.lower()}@example.com",
        role="cliente",
        is_active=True,
        created_at=utc_now_naive(),
        updated_at=utc_now_naive(),
        fecha_registro=utc_now_naive(),
        fecha_ultima_actividad=utc_now_naive(),
        total_solicitudes=1,
    )
    db.session.add(cliente)
    db.session.flush()
    solicitud = Solicitud(
        cliente_id=int(cliente.id),
        fecha_solicitud=utc_now_naive(),
        codigo_solicitud=solicitud_codigo,
        tipo_plan=tipo_plan,
    )
    db.session.add(solicitud)
    db.session.commit()
    return cliente, solicitud


@pytest.mark.parametrize(
    ("submitted_data", "expected_plan"),
    [
        ({"tipo_plan": "basico"}, "basico"),
        ({"tipo_plan": "premium"}, "premium"),
        ({"tipo_plan": "vip"}, "vip"),
        (MultiDict([("tipo_plan_visual", "basico"), ("tipo_plan", "vip")]), "vip"),
        (MultiDict([("tipo_plan", "basico"), ("tipo_plan", "vip")]), "vip"),
    ],
)
def test_cliente_autenticado_boton_guarda_plan_correcto_y_no_toca_payment_cycle(submitted_data, expected_plan):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        ensure_sqlite_compat_tables([Cliente, Solicitud], reset=True)
        cliente, solicitud = _seed_cliente_y_solicitud(codigo="CL-PLAN-01", solicitud_codigo="CL-PLAN-01-A")
        fake_user = SimpleNamespace(id=int(cliente.id), is_authenticated=True, role="cliente")
        target = _unwrap_view(clientes_routes.solicitud_elegir_plan, 2)

        with patch.object(clientes_routes, "current_user", fake_user):
            with flask_app.test_request_context(
                f"/clientes/solicitudes/{solicitud.id}/plan",
                method="POST",
                data=submitted_data,
            ):
                resp = target(int(solicitud.id))

        updated = Solicitud.query.get(int(solicitud.id))
        assert resp.status_code in (302, 303)
        assert f"/clientes/solicitudes/{solicitud.id}/plan/resumen" in (resp.location or "")
        assert updated is not None
        assert updated.tipo_plan == expected_plan
        assert updated.payment_cycle_plan is None
        assert updated.payment_cycle_precio_total is None
        assert updated.payment_cycle_abono_requerido is None
        assert updated.payment_cycle_opened_at is None
        assert updated.payment_cycle_closed_at is None
        assert updated.payment_cycle_motivo_apertura is None


def test_cliente_autenticado_plan_invalido_no_guarda():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        ensure_sqlite_compat_tables([Cliente, Solicitud], reset=True)
        cliente, solicitud = _seed_cliente_y_solicitud(codigo="CL-PLAN-02", solicitud_codigo="CL-PLAN-02-A")
        fake_user = SimpleNamespace(id=int(cliente.id), is_authenticated=True, role="cliente")
        target = _unwrap_view(clientes_routes.solicitud_elegir_plan, 2)

        with patch.object(clientes_routes, "current_user", fake_user):
            with flask_app.test_request_context(
                f"/clientes/solicitudes/{solicitud.id}/plan",
                method="POST",
                data={"tipo_plan": "oro"},
            ):
                resp = target(int(solicitud.id))

        updated = Solicitud.query.get(int(solicitud.id))
        assert updated is not None
        assert updated.tipo_plan is None
        assert isinstance(resp, str)


def test_token_existente_reanuda_seleccion_si_solicitud_quedo_sin_plan():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        ensure_sqlite_compat_tables([Cliente, Solicitud, PublicSolicitudTokenUso], reset=True)
        cliente, solicitud = _seed_cliente_y_solicitud(codigo="CL-PUB-01", solicitud_codigo="CL-PUB-01-A")
        db.session.add(
            PublicSolicitudTokenUso(
                token_hash=clientes_routes._public_link_token_hash_storage("tok-resume"),
                cliente_id=int(cliente.id),
                solicitud_id=int(solicitud.id),
                consumption_reason="plan_pending",
                public_form_source="cliente_existente",
                used_at=utc_now_naive(),
            )
        )
        db.session.commit()

    resp = client.get("/clientes/solicitudes/publica/tok-resume", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/clientes/solicitudes/publica/tok-resume/plan" in (resp.location or "")


def test_token_existente_alias_corto_reanuda_seleccion_si_solicitud_quedo_sin_plan():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        ensure_sqlite_compat_tables([Cliente, Solicitud, PublicSolicitudTokenUso], reset=True)
        cliente, solicitud = _seed_cliente_y_solicitud(codigo="CL-PUB-01B", solicitud_codigo="CL-PUB-01B-A")
        db.session.add(
            PublicSolicitudTokenUso(
                token_hash=clientes_routes._public_link_token_hash_storage("tok-resume-short"),
                cliente_id=int(cliente.id),
                solicitud_id=int(solicitud.id),
                consumption_reason="plan_pending",
                public_form_source="cliente_existente",
                used_at=utc_now_naive(),
            )
        )
        db.session.commit()

    resp = client.get("/clientes/f/tok-resume-short", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/clientes/solicitudes/publica/tok-resume-short/plan" in (resp.location or "")


@pytest.mark.parametrize(
    ("submitted_data", "expected_plan"),
    [
        ({"tipo_plan": "vip"}, "vip"),
        (MultiDict([("tipo_plan_visual", "basico"), ("tipo_plan", "vip")]), "vip"),
    ],
)
def test_token_existente_post_plan_guarda_tipo_plan_correcto_sin_tocar_pagos(submitted_data, expected_plan):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        ensure_sqlite_compat_tables([Cliente, Solicitud, PublicSolicitudTokenUso], reset=True)
        cliente, solicitud = _seed_cliente_y_solicitud(codigo="CL-PUB-02", solicitud_codigo="CL-PUB-02-A")
        db.session.add(
            PublicSolicitudTokenUso(
                token_hash=clientes_routes._public_link_token_hash_storage("tok-save"),
                cliente_id=int(cliente.id),
                solicitud_id=int(solicitud.id),
                consumption_reason="plan_pending",
                public_form_source="cliente_existente",
                used_at=utc_now_naive(),
            )
        )
        db.session.commit()

        resp = client.post("/clientes/solicitudes/publica/tok-save/plan", data=submitted_data, follow_redirects=False)
        updated = Solicitud.query.get(int(solicitud.id))

    assert resp.status_code in (302, 303)
    assert "/clientes/solicitudes/publica/tok-save/plan/resumen" in (resp.location or "")
    assert updated is not None
    assert updated.tipo_plan == expected_plan
    assert updated.payment_cycle_plan is None
    assert updated.payment_cycle_precio_total is None
    assert updated.payment_cycle_abono_requerido is None


@pytest.mark.parametrize(
    ("submitted_data", "expected_plan"),
    [
        ({"tipo_plan": "basico"}, "basico"),
        ({"tipo_plan": "premium"}, "premium"),
        (MultiDict([("tipo_plan_visual", "basico"), ("tipo_plan", "vip")]), "vip"),
    ],
)
def test_token_nuevo_post_plan_guarda_tipo_plan_correcto_sin_tocar_pagos(submitted_data, expected_plan):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        ensure_sqlite_compat_tables([Cliente, Solicitud, PublicSolicitudClienteNuevoTokenUso], reset=True)
        cliente, solicitud = _seed_cliente_y_solicitud(codigo="CL-PUB-03", solicitud_codigo="CL-PUB-03-A")
        db.session.add(
            PublicSolicitudClienteNuevoTokenUso(
                token_hash=clientes_routes._public_link_token_hash_storage("tok-new-save"),
                cliente_id=int(cliente.id),
                solicitud_id=int(solicitud.id),
                consumption_reason="plan_pending",
                public_form_source="cliente_nuevo",
                used_at=utc_now_naive(),
            )
        )
        db.session.commit()

        resp = client.post("/clientes/solicitudes/nueva-publica/tok-new-save/plan", data=submitted_data, follow_redirects=False)
        updated = Solicitud.query.get(int(solicitud.id))

    assert resp.status_code in (302, 303)
    assert "/clientes/solicitudes/nueva-publica/tok-new-save/plan/resumen" in (resp.location or "")
    assert updated is not None
    assert updated.tipo_plan == expected_plan
    assert updated.payment_cycle_plan is None
    assert updated.payment_cycle_precio_total is None
    assert updated.payment_cycle_abono_requerido is None


def test_token_nuevo_alias_corto_reanuda_seleccion_si_solicitud_quedo_sin_plan():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        ensure_sqlite_compat_tables([Cliente, Solicitud, PublicSolicitudClienteNuevoTokenUso], reset=True)
        cliente, solicitud = _seed_cliente_y_solicitud(codigo="CL-PUB-03B", solicitud_codigo="CL-PUB-03B-A")
        db.session.add(
            PublicSolicitudClienteNuevoTokenUso(
                token_hash=clientes_routes._public_link_token_hash_storage("tok-new-short"),
                cliente_id=int(cliente.id),
                solicitud_id=int(solicitud.id),
                consumption_reason="plan_pending",
                public_form_source="cliente_nuevo",
                used_at=utc_now_naive(),
            )
        )
        db.session.commit()

    resp = client.get("/clientes/n/tok-new-short", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/clientes/solicitudes/nueva-publica/tok-new-short/plan" in (resp.location or "")
