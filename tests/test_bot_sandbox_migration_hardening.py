from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


MIGRATION_PATH = Path("migrations/versions/20260511_1200_create_bot_sandbox_outbox.py").resolve()


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("bot_migration_20260511_1200", str(MIGRATION_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bot_sandbox_outbox_migration_upgrade_downgrade_idempotent_sqlite():
    tmp_db = Path(tempfile.gettempdir()) / "app_web_bot_sandbox_outbox_migration.sqlite"
    if tmp_db.exists():
        tmp_db.unlink()
    engine = create_engine(f"sqlite:///{tmp_db}")
    migration = _load_migration_module()

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE bot_conversations (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE bot_messages (id INTEGER PRIMARY KEY)"))

        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()
            migration.upgrade()

        insp = inspect(conn)
        assert "bot_sandbox_outbox" in set(insp.get_table_names())
        indexes = {ix.get("name") for ix in insp.get_indexes("bot_sandbox_outbox")}
        assert "ix_bot_sandbox_outbox_state_retry" in indexes
        assert "ix_bot_sandbox_outbox_conversation_created" in indexes
        assert "ix_bot_sandbox_outbox_state_created" in indexes

        rows = conn.execute(text("PRAGMA index_list('bot_sandbox_outbox')")).fetchall()
        names = {str(r[1]) for r in rows}
        assert "uq_bot_sandbox_outbox_message_id" in names or any("bot_message_id" in n for n in names)

        with Operations.context(ctx):
            migration.downgrade()

        assert "bot_sandbox_outbox" not in set(inspect(conn).get_table_names())
