# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import app as flask_app
from config_app import db
from models import Cliente, PublicSolicitudClienteNuevoTokenUso, PublicSolicitudTokenUso, Solicitud
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.robust_save import execute_robust_save
from utils.timezone import utc_now_naive


def _reset_public_intake_tables() -> None:
    ensure_sqlite_compat_tables(
        [Cliente, Solicitud, PublicSolicitudTokenUso, PublicSolicitudClienteNuevoTokenUso],
        reset=True,
    )
    db.session.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_public_solicitud_tokens_usados_token_hash "
            "ON public_solicitud_tokens_usados(token_hash)"
        )
    )
    db.session.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_public_solicitud_cliente_nuevo_tokens_usados_token_hash "
            "ON public_solicitud_cliente_nuevo_tokens_usados(token_hash)"
        )
    )
    db.session.commit()


def _seed_cliente(*, codigo: str, nombre: str, email: str) -> Cliente:
    now_ref = utc_now_naive()
    cliente = Cliente(
        codigo=codigo,
        nombre_completo=nombre,
        email=email,
        role="cliente",
        is_active=True,
        created_at=now_ref,
        updated_at=now_ref,
        fecha_registro=now_ref,
        fecha_ultima_actividad=now_ref,
        total_solicitudes=0,
    )
    db.session.add(cliente)
    db.session.commit()
    return cliente


def _audit_submitted_usage_graph(model_cls) -> None:
    rows = model_cls.query.filter_by(consumption_reason="submitted").all()
    for row in rows:
        solicitud_id = int(getattr(row, "solicitud_id", 0) or 0)
        cliente_id = int(getattr(row, "cliente_id", 0) or 0)
        assert solicitud_id > 0, f"{model_cls.__name__} submitted row without solicitud_id"

        solicitud = Solicitud.query.filter_by(id=solicitud_id).first()
        assert solicitud is not None, f"{model_cls.__name__} submitted row points to missing solicitud"

        solicitud_cliente_id = int(getattr(solicitud, "cliente_id", 0) or 0)
        assert solicitud_cliente_id > 0, "solicitud without cliente_id"

        cliente = Cliente.query.filter_by(id=solicitud_cliente_id).first()
        assert cliente is not None, "solicitud points to missing cliente"

        if cliente_id > 0:
            assert cliente_id == solicitud_cliente_id, "usage.cliente_id does not match solicitud.cliente_id"


@pytest.fixture()
def public_link_db_env():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.app_context():
        _reset_public_intake_tables()
        yield
        db.session.rollback()


def test_new_public_flow_creates_cliente_solicitud_and_token_usage_in_single_transaction(public_link_db_env):
    token_hash = "tok-new-integrity-1"
    now_ref = utc_now_naive()
    state = {"cliente_id": 0, "solicitud_id": 0}

    def _persist(_attempt: int) -> None:
        usage = PublicSolicitudClienteNuevoTokenUso(
            token_hash=token_hash,
            cliente_id=None,
            solicitud_id=None,
            consumption_reason="submitted",
            public_form_source="cliente_nuevo",
            used_at=now_ref,
        )
        db.session.add(usage)
        db.session.flush()

        cliente = Cliente(
            codigo="CL-DB-NEW-01",
            nombre_completo="Cliente Nuevo DB",
            email="db.new@example.com",
            role="cliente",
            is_active=True,
            created_at=now_ref,
            updated_at=now_ref,
            fecha_registro=now_ref,
            fecha_ultima_actividad=now_ref,
            total_solicitudes=0,
        )
        db.session.add(cliente)
        db.session.flush()

        solicitud = Solicitud(
            cliente_id=int(cliente.id),
            fecha_solicitud=now_ref,
            codigo_solicitud="CL-DB-NEW-01-A",
        )
        db.session.add(solicitud)
        db.session.flush()

        usage.cliente_id = int(cliente.id)
        usage.solicitud_id = int(solicitud.id)
        cliente.total_solicitudes = 1
        cliente.fecha_ultima_solicitud = now_ref

        state["cliente_id"] = int(cliente.id)
        state["solicitud_id"] = int(solicitud.id)

    def _verify() -> bool:
        return (
            int(state["cliente_id"]) > 0
            and int(state["solicitud_id"]) > 0
            and Cliente.query.filter_by(id=state["cliente_id"]).count() == 1
            and Solicitud.query.filter_by(id=state["solicitud_id"], cliente_id=state["cliente_id"]).count() == 1
            and PublicSolicitudClienteNuevoTokenUso.query.filter_by(
                token_hash=token_hash,
                cliente_id=state["cliente_id"],
                solicitud_id=state["solicitud_id"],
                consumption_reason="submitted",
            ).count() == 1
        )

    result = execute_robust_save(
        session=db.session,
        persist_fn=_persist,
        verify_fn=_verify,
        max_retries=1,
        retryable_exceptions=(IntegrityError, SQLAlchemyError),
    )

    assert result.ok is True
    assert Cliente.query.count() == 1
    assert Solicitud.query.count() == 1
    assert PublicSolicitudClienteNuevoTokenUso.query.count() == 1
    _audit_submitted_usage_graph(PublicSolicitudClienteNuevoTokenUso)


def test_existing_public_flow_creates_solicitud_without_duplicating_cliente(public_link_db_env):
    cliente = _seed_cliente(
        codigo="CL-DB-EX-01",
        nombre="Cliente Existente DB",
        email="db.existente@example.com",
    )
    token_hash = "tok-existing-integrity-1"
    now_ref = utc_now_naive()
    solicitud_id_holder = {"value": 0}

    def _persist(_attempt: int) -> None:
        usage = PublicSolicitudTokenUso(
            token_hash=token_hash,
            cliente_id=int(cliente.id),
            solicitud_id=None,
            consumption_reason="submitted",
            public_form_source="cliente_existente",
            used_at=now_ref,
        )
        db.session.add(usage)
        db.session.flush()

        solicitud = Solicitud(
            cliente_id=int(cliente.id),
            fecha_solicitud=now_ref,
            codigo_solicitud="CL-DB-EX-01-A",
        )
        db.session.add(solicitud)
        db.session.flush()

        usage.solicitud_id = int(solicitud.id)
        cliente.total_solicitudes = int(cliente.total_solicitudes or 0) + 1
        cliente.fecha_ultima_solicitud = now_ref
        solicitud_id_holder["value"] = int(solicitud.id)

    def _verify() -> bool:
        solicitud_id = int(solicitud_id_holder["value"] or 0)
        return (
            solicitud_id > 0
            and Cliente.query.count() == 1
            and Solicitud.query.filter_by(id=solicitud_id, cliente_id=int(cliente.id)).count() == 1
            and PublicSolicitudTokenUso.query.filter_by(
                token_hash=token_hash,
                cliente_id=int(cliente.id),
                solicitud_id=solicitud_id,
                consumption_reason="submitted",
            ).count() == 1
        )

    result = execute_robust_save(
        session=db.session,
        persist_fn=_persist,
        verify_fn=_verify,
        max_retries=1,
        retryable_exceptions=(IntegrityError, SQLAlchemyError),
    )

    assert result.ok is True
    assert Cliente.query.count() == 1
    assert Solicitud.query.count() == 1
    assert PublicSolicitudTokenUso.query.count() == 1
    _audit_submitted_usage_graph(PublicSolicitudTokenUso)


def test_public_usage_audit_rejects_submitted_token_without_solicitud(public_link_db_env):
    cliente = _seed_cliente(
        codigo="CL-DB-BAD-01",
        nombre="Cliente Invalido DB",
        email="db.bad@example.com",
    )
    row = PublicSolicitudTokenUso(
        token_hash="tok-missing-solicitud",
        cliente_id=int(cliente.id),
        solicitud_id=None,
        consumption_reason="submitted",
        public_form_source="cliente_existente",
        used_at=utc_now_naive(),
    )
    db.session.add(row)
    db.session.commit()

    with pytest.raises(AssertionError, match="without solicitud_id"):
        _audit_submitted_usage_graph(PublicSolicitudTokenUso)


def test_public_usage_audit_rejects_solicitud_without_existing_cliente(public_link_db_env):
    solicitud = Solicitud(
        cliente_id=999999,
        fecha_solicitud=utc_now_naive(),
        codigo_solicitud="SOL-DB-ORPHAN-A",
    )
    db.session.add(solicitud)
    db.session.flush()

    usage = PublicSolicitudClienteNuevoTokenUso(
        token_hash="tok-orphan-graph",
        cliente_id=999999,
        solicitud_id=int(solicitud.id),
        consumption_reason="submitted",
        public_form_source="cliente_nuevo",
        used_at=utc_now_naive(),
    )
    db.session.add(usage)
    db.session.commit()

    with pytest.raises(AssertionError, match="solicitud points to missing cliente"):
        _audit_submitted_usage_graph(PublicSolicitudClienteNuevoTokenUso)


def test_same_token_hash_can_create_only_one_solicitud_under_concurrency_retry(public_link_db_env):
    cliente = _seed_cliente(
        codigo="CL-DB-CONC-01",
        nombre="Cliente Concurrencia DB",
        email="db.conc@example.com",
    )
    token_hash = "tok-same-concurrency"
    now_ref = utc_now_naive()

    first_usage = PublicSolicitudTokenUso(
        token_hash=token_hash,
        cliente_id=int(cliente.id),
        solicitud_id=None,
        consumption_reason="submitted",
        public_form_source="cliente_existente",
        used_at=now_ref,
    )
    db.session.add(first_usage)
    db.session.flush()

    first_solicitud = Solicitud(
        cliente_id=int(cliente.id),
        fecha_solicitud=now_ref,
        codigo_solicitud="CL-DB-CONC-01-A",
    )
    db.session.add(first_solicitud)
    db.session.flush()
    first_usage.solicitud_id = int(first_solicitud.id)
    db.session.commit()

    def _persist_duplicate(_attempt: int) -> None:
        duplicate_usage = PublicSolicitudTokenUso(
            token_hash=token_hash,
            cliente_id=int(cliente.id),
            solicitud_id=None,
            consumption_reason="submitted",
            public_form_source="cliente_existente",
            used_at=now_ref,
        )
        db.session.add(duplicate_usage)
        db.session.flush()

        duplicate_solicitud = Solicitud(
            cliente_id=int(cliente.id),
            fecha_solicitud=now_ref,
            codigo_solicitud="CL-DB-CONC-01-B",
        )
        db.session.add(duplicate_solicitud)

    result = execute_robust_save(
        session=db.session,
        persist_fn=_persist_duplicate,
        verify_fn=lambda: False,
        max_retries=1,
        retryable_exceptions=(IntegrityError, SQLAlchemyError),
    )

    assert result.ok is False
    assert PublicSolicitudTokenUso.query.filter_by(token_hash=token_hash).count() == 1
    assert Solicitud.query.filter_by(cliente_id=int(cliente.id)).count() == 1
    _audit_submitted_usage_graph(PublicSolicitudTokenUso)
