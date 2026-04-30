# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import app as flask_app
from config_app import db
from models import Cliente
import clientes.routes as clientes_routes
from utils.client_contact_norm import nullable_norm_phone_rd


def _ensure_norm_columns():
    cols = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(clientes)")).fetchall()
    }
    if "email_norm" not in cols:
        db.session.execute(text("ALTER TABLE clientes ADD COLUMN email_norm VARCHAR(100)"))
    if "telefono_norm" not in cols:
        db.session.execute(text("ALTER TABLE clientes ADD COLUMN telefono_norm VARCHAR(32)"))
    db.session.commit()


def _clear_historical_duplicates_for_index(column_name: str):
    # Mantiene el registro más antiguo por valor y limpia el resto a NULL
    rows = db.session.execute(
        text(
            f"""
            SELECT {column_name} AS v, MIN(id) AS keep_id
            FROM clientes
            WHERE {column_name} IS NOT NULL AND {column_name} <> ''
            GROUP BY {column_name}
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    for row in rows:
        db.session.execute(
            text(
                f"""
                UPDATE clientes
                SET {column_name} = NULL
                WHERE {column_name} = :v AND id <> :keep_id
                """
            ),
            {"v": row[0], "keep_id": int(row[1])},
        )
    db.session.commit()


def _mk_cliente(*, codigo: str, nombre: str, email: str, telefono: str) -> Cliente:
    now = datetime.utcnow()
    return Cliente(
        codigo=codigo,
        nombre_completo=nombre,
        email=email,
        telefono=telefono,
        role="cliente",
        is_active=True,
        fecha_registro=now,
        created_at=now,
        updated_at=now,
    )


def test_cliente_norm_fields_are_autofilled_by_model_events():
    with flask_app.app_context():
        _ensure_norm_columns()
        stamp = str(int(datetime.utcnow().timestamp() * 1000000))
        c = _mk_cliente(
            codigo=f"DUPE-NORM-{stamp}",
            nombre="Cliente Norm",
            email=f"  TEST{stamp}@Example.Com ",
            telefono="+1 (809) 123-4567",
        )
        db.session.add(c)
        db.session.commit()
        got = Cliente.query.filter_by(id=c.id).first()
        assert got is not None
        assert got.email_norm == f"test{stamp}@example.com"
        assert got.telefono_norm == "8091234567"
        db.session.execute(text("DELETE FROM clientes WHERE id = :id"), {"id": int(c.id)})
        db.session.commit()


def test_db_unique_email_norm_blocks_duplicate_even_if_prevalidation_fails():
    with flask_app.app_context():
        _ensure_norm_columns()
        _clear_historical_duplicates_for_index("email_norm")
        db.session.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_test_clientes_email_norm "
                "ON clientes(email_norm) WHERE email_norm IS NOT NULL AND email_norm <> ''"
            )
        )
        db.session.commit()
        try:
            a = _mk_cliente(codigo="DUPE-EMAIL-1", nombre="A", email="uno@example.com", telefono="8091110001")
            b = _mk_cliente(codigo="DUPE-EMAIL-2", nombre="B", email=" UNO@EXAMPLE.COM ", telefono="8091110002")
            db.session.add(a)
            db.session.commit()
            db.session.add(b)
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()
        finally:
            db.session.execute(text("DROP INDEX IF EXISTS uq_test_clientes_email_norm"))
            db.session.commit()
            Cliente.query.filter(Cliente.codigo.like("DUPE-EMAIL-%")).delete(synchronize_session=False)
            db.session.commit()


def test_db_unique_telefono_norm_blocks_duplicate_even_if_prevalidation_fails():
    with flask_app.app_context():
        _ensure_norm_columns()
        _clear_historical_duplicates_for_index("telefono_norm")
        db.session.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_test_clientes_telefono_norm "
                "ON clientes(telefono_norm) WHERE telefono_norm IS NOT NULL AND telefono_norm <> ''"
            )
        )
        db.session.commit()
        try:
            a = _mk_cliente(codigo="DUPE-PHONE-1", nombre="A", email="ph1@example.com", telefono="(809) 222-3333")
            b = _mk_cliente(codigo="DUPE-PHONE-2", nombre="B", email="ph2@example.com", telefono="+1-809-222-3333")
            db.session.add(a)
            db.session.commit()
            db.session.add(b)
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()
        finally:
            db.session.execute(text("DROP INDEX IF EXISTS uq_test_clientes_telefono_norm"))
            db.session.commit()
            Cliente.query.filter(Cliente.codigo.like("DUPE-PHONE-%")).delete(synchronize_session=False)
            db.session.commit()


def test_two_distinct_tokens_second_attempt_is_blocked_by_duplicate_detection():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    fake_form = type("F", (), {})()
    fake_form.hp = SimpleNamespace(data="")
    fake_form.nombre_completo = SimpleNamespace(data="Cliente Token")
    fake_form.email_contacto = SimpleNamespace(data="token@example.com")
    fake_form.telefono_contacto = SimpleNamespace(data="809-444-5555")
    fake_form.ciudad_cliente = SimpleNamespace(data="Santiago")
    fake_form.sector_cliente = SimpleNamespace(data="Centro")
    fake_form.ciudad_sector = SimpleNamespace(data="Santiago/Centro")
    fake_form.funciones = SimpleNamespace(data=["limpieza"])
    fake_form.funciones_otro = SimpleNamespace(data="")
    fake_form.areas_comunes = SimpleNamespace(data=["sala"], choices=[("sala", "Sala")])
    fake_form.area_otro = SimpleNamespace(data="")
    fake_form.edad_requerida = SimpleNamespace(data=["26-35"], choices=[("26-35", "26-35")])
    fake_form.edad_otro = SimpleNamespace(data="")
    fake_form.tipo_lugar_otro = SimpleNamespace(data="")
    fake_form.mascota = SimpleNamespace(data="")
    fake_form.nota_cliente = SimpleNamespace(data="")
    fake_form.sueldo = SimpleNamespace(data="18000")
    fake_form.dos_pisos = SimpleNamespace(data=False)
    fake_form.pasaje_aporte = SimpleNamespace(data=False)
    fake_form.validate_on_submit = lambda: True
    fake_form.populate_obj = lambda obj: obj

    dup = SimpleNamespace(id=987, email="token@example.com", telefono="8094445555")
    side_effect = [(None, ""), (dup, "email")]
    with patch("clientes.routes.SolicitudClienteNuevoPublicaForm", return_value=fake_form), \
         patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
         patch("clientes.routes._public_new_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._resolve_public_new_link_token", return_value=(True, "", {})), \
         patch("clientes.routes._find_cliente_contact_duplicate", side_effect=side_effect), \
         patch("clientes.routes.execute_robust_save", return_value=SimpleNamespace(ok=True, attempts=1, error_message="")):
        first = client.post("/clientes/solicitudes/nueva-publica/tokenA", data={"dummy": "1"}, follow_redirects=False)
        second = client.post("/clientes/solicitudes/nueva-publica/tokenB", data={"dummy": "1"}, follow_redirects=False)

    assert first.status_code in (302, 303)
    assert second.status_code == 409


def test_invalid_phone_placeholders_are_normalized_to_null():
    assert nullable_norm_phone_rd("0000000000") is None
    assert nullable_norm_phone_rd("1111111111") is None
    assert nullable_norm_phone_rd("1234567890") is None
    assert nullable_norm_phone_rd("2222222222") is None
    assert nullable_norm_phone_rd("8091234") is None


def test_duplicate_detection_ignores_invalid_placeholder_phone():
    with flask_app.app_context():
        original_query = clientes_routes.Cliente.query
        fake_rows = [(1, "0000000000"), (2, "(000)000-0000")]

        class _Q:
            def filter(self, *_args, **_kwargs):
                return self

            def with_entities(self, *_args, **_kwargs):
                return self

            def all(self):
                return fake_rows

            def filter_by(self, **_kwargs):
                return self

            def first(self):
                return None

        clientes_routes.Cliente.query = _Q()
        try:
            row, field = clientes_routes._find_cliente_contact_duplicate("", "0000000000")
            assert row is None
            assert field == ""
        finally:
            clientes_routes.Cliente.query = original_query
