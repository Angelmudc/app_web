# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from app import app as flask_app
import app as app_module
from config_app import db
from models import Cliente
from sqlalchemy import text


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


def test_export_clientes_duplicados_generates_csv_with_expected_columns():
    flask_app.config["TESTING"] = True
    suffix = uuid.uuid4().hex[:8]
    with flask_app.app_context():
        _ensure_norm_columns()
        c1 = _mk_cliente(
            codigo=f"CSV-DUP-{suffix}-1",
            nombre="CSV Uno",
            email=f"csv1_{suffix}@example.com",
            telefono="(809) 999-1212",
        )
        c2 = _mk_cliente(
            codigo=f"CSV-DUP-{suffix}-2",
            nombre="CSV Dos",
            email=f"csv2_{suffix}@example.com",
            telefono="+1 809 999 1212",
        )
        db.session.add(c1)
        db.session.add(c2)
        db.session.commit()

    out_file = Path(tempfile.gettempdir()) / f"clientes_duplicados_{suffix}.csv"
    if out_file.exists():
        out_file.unlink()

    runner = flask_app.test_cli_runner()
    result = runner.invoke(args=["export-clientes-duplicados", "--output", str(out_file)])

    assert result.exit_code == 0
    assert out_file.exists()
    assert "CSV generado:" in result.output

    with out_file.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        assert headers == [
            "cliente_id",
            "nombre",
            "email",
            "email_norm",
            "telefono",
            "telefono_norm",
            "fecha_creacion",
            "campo_duplicado",
            "valor_duplicado",
        ]
        rows = list(reader)
        assert any(r.get("campo_duplicado") == "telefono_norm" for r in rows)

    with flask_app.app_context():
        Cliente.query.filter(Cliente.codigo.like(f"CSV-DUP-{suffix}-%")).delete(synchronize_session=False)
        db.session.commit()


def test_auditoria_ignora_placeholder_0000000000_en_telefono_norm():
    flask_app.config["TESTING"] = True
    suffix = uuid.uuid4().hex[:8]
    with flask_app.app_context():
        _ensure_norm_columns()
        a = _mk_cliente(
            codigo=f"CSV-PLACE-{suffix}-1",
            nombre="Place Uno",
            email=f"place1_{suffix}@example.com",
            telefono="0000000000",
        )
        b = _mk_cliente(
            codigo=f"CSV-PLACE-{suffix}-2",
            nombre="Place Dos",
            email=f"place2_{suffix}@example.com",
            telefono="0000000000",
        )
        db.session.add(a)
        db.session.add(b)
        db.session.commit()
        db.session.execute(
            text("UPDATE clientes SET telefono_norm='0000000000' WHERE codigo LIKE :p"),
            {"p": f"CSV-PLACE-{suffix}-%"},
        )
        db.session.commit()

        dup_rows = app_module._collect_clientes_duplicados_rows()
        phone_rows = [r for r in dup_rows if (r.get("campo_duplicado") or "") == "telefono_norm"]
        assert not any((r.get("valor_duplicado") or "") == "0000000000" for r in phone_rows)

        Cliente.query.filter(Cliente.codigo.like(f"CSV-PLACE-{suffix}-%")).delete(synchronize_session=False)
        db.session.commit()
