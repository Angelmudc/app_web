# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


BOT_TABLES = {
    "bot_contact_identities",
    "bot_conversations",
    "bot_messages",
    "bot_decision_logs",
    "bot_settings",
    "bot_escalations",
}


def _load_migration_module():
    migration_path = Path("migrations/versions/20260508_1100_create_bot_internal_tables.py").resolve()
    spec = importlib.util.spec_from_file_location("bot_migration_20260508_1100", str(migration_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bot_migration_upgrade_downgrade_equivalent_cycle_local_sqlite():
    tmp_db = Path(tempfile.gettempdir()) / "app_web_bot_migration_equivalent_cycle.sqlite"
    if tmp_db.exists():
        tmp_db.unlink()
    engine = create_engine(f"sqlite:///{tmp_db}")
    migration = _load_migration_module()

    with engine.begin() as conn:
        # Stubs legacy mínimas para satisfacer FKs de la migración bot.
        conn.execute(text("CREATE TABLE clientes (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE candidatas (fila INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE staff_users (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE legacy_sentinel (id INTEGER PRIMARY KEY, note TEXT)"))
        conn.execute(text("INSERT INTO legacy_sentinel (id, note) VALUES (1, 'keep')"))

        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()

        insp = inspect(conn)
        table_names = set(insp.get_table_names())
        assert BOT_TABLES.issubset(table_names)
        assert "legacy_sentinel" in table_names

        conv_indexes = {ix.get("name") for ix in insp.get_indexes("bot_conversations")}
        assert "ix_bot_conv_status_paused_last_msg" in conv_indexes
        assert "ix_bot_conversations_phone_e164" in conv_indexes

        with Operations.context(ctx):
            migration.downgrade()

        insp = inspect(conn)
        table_names_after = set(insp.get_table_names())
        assert not (BOT_TABLES & table_names_after)
        assert "legacy_sentinel" in table_names_after
        assert "clientes" in table_names_after
        assert "candidatas" in table_names_after
        assert "staff_users" in table_names_after

        sentinel = conn.execute(text("SELECT note FROM legacy_sentinel WHERE id = 1")).scalar_one()
        assert sentinel == "keep"
