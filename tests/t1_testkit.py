# -*- coding: utf-8 -*-
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.sql.sqltypes import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
)

from config_app import db


def _patch_postgres_types_for_sqlite(models: list[type]) -> None:
    for model in models:
        for col in model.__table__.columns:
            if isinstance(col.type, (ARRAY, JSONB)):
                col.type = db.Text()
                col.default = None
                col.server_default = None


def _sqlite_col_type(col) -> str:
    t = col.type
    if isinstance(t, (Integer, BigInteger, SmallInteger)):
        return "INTEGER"
    if isinstance(t, (Numeric, Float)):
        return "NUMERIC"
    if isinstance(t, Boolean):
        return "BOOLEAN"
    if isinstance(t, DateTime):
        return "DATETIME"
    if isinstance(t, Date):
        return "DATE"
    if isinstance(t, LargeBinary):
        return "BLOB"
    return "TEXT"


def ensure_sqlite_compat_tables(models: list[type], *, reset: bool = False) -> None:
    """Create SQLite-friendly tables for mapped models when they do not exist.

    These tables are intentionally relaxed (few constraints) so integration tests
    can run against sqlite even when ORM models contain PostgreSQL-only defaults.
    """
    bind = db.engine
    inspector = inspect(bind)

    _patch_postgres_types_for_sqlite(models)

    with bind.begin() as conn:
        for model in models:
            table = model.__table__
            table_name = table.name
            exists = inspector.has_table(table_name)
            if exists and reset:
                conn.execute(text(f'DROP TABLE IF EXISTS \"{table_name}\"'))
                exists = False
            if exists:
                continue

            pk_cols = [c for c in table.columns if c.primary_key]
            single_int_pk = (
                len(pk_cols) == 1 and isinstance(pk_cols[0].type, (Integer, BigInteger, SmallInteger))
            )

            col_defs: list[str] = []
            composite_pk: list[str] = []
            for col in table.columns:
                col_name = str(col.name)
                col_type = _sqlite_col_type(col)

                if col.primary_key and single_int_pk and col is pk_cols[0]:
                    col_defs.append(f'"{col_name}" INTEGER PRIMARY KEY')
                    continue

                col_defs.append(f'"{col_name}" {col_type}')
                if col.primary_key:
                    composite_pk.append(f'"{col_name}"')

            if composite_pk and not single_int_pk:
                col_defs.append(f"PRIMARY KEY ({', '.join(composite_pk)})")

            ddl = f'CREATE TABLE IF NOT EXISTS "{table_name}" ({", ".join(col_defs)})'
            conn.execute(text(ddl))
            if table_name == "outbox_consumer_receipts":
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_outbox_consumer_event "
                        "ON outbox_consumer_receipts(consumer_name, event_id)"
                    )
                )
            if table_name == "request_idempotency_keys":
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_request_idempotency_scope_key "
                        "ON request_idempotency_keys(scope, idempotency_key)"
                    )
                )
