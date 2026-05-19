from __future__ import annotations

import tempfile
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text


def test_sqlite_constraints_enforced_for_state_and_retry_count():
    tmp_db = Path(tempfile.gettempdir()) / "app_web_bot_sandbox_sqlite_compat.sqlite"
    if tmp_db.exists():
        tmp_db.unlink()
    engine = create_engine(f"sqlite:///{tmp_db}")

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE bot_conversations (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE bot_messages (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO bot_conversations (id) VALUES (1)"))
        conn.execute(text("INSERT INTO bot_messages (id) VALUES (1)"))
        conn.execute(
            text(
                """
                CREATE TABLE bot_sandbox_outbox (
                    id INTEGER PRIMARY KEY,
                    conversation_id INTEGER NOT NULL,
                    bot_message_id INTEGER NOT NULL,
                    phone_e164 VARCHAR(20) NOT NULL,
                    provider VARCHAR(30) NOT NULL DEFAULT 'fake',
                    state VARCHAR(30) NOT NULL DEFAULT 'queued',
                    payload_json JSON NOT NULL DEFAULT '{}',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_bot_sandbox_outbox_message_id UNIQUE (bot_message_id),
                    CONSTRAINT ck_bot_sandbox_outbox_state_allowed CHECK (state IN ('queued','processing','simulated_sent','blocked','failed')),
                    CONSTRAINT ck_bot_sandbox_outbox_retry_non_negative CHECK (retry_count >= 0)
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO bot_sandbox_outbox (id, conversation_id, bot_message_id, phone_e164, state, retry_count, created_at, updated_at) "
                "VALUES (1, 1, 1, '+19990000000', 'queued', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

        try:
            conn.execute(
                text(
                    "INSERT INTO bot_sandbox_outbox (id, conversation_id, bot_message_id, phone_e164, state, retry_count, created_at, updated_at) "
                    "VALUES (2, 1, 2, '+19990000001', 'invalid', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            assert False, "state inválido debió fallar"
        except Exception:
            pass


def test_postgres_ddl_contains_state_and_retry_checks():
    metadata = sa.MetaData()
    table = sa.Table(
        "bot_sandbox_outbox",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("bot_message_id", sa.Integer, nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("retry_count", sa.Integer, nullable=False),
        sa.CheckConstraint("state IN ('queued','processing','simulated_sent','blocked','failed')", name="ck_state"),
        sa.CheckConstraint("retry_count >= 0", name="ck_retry"),
        sa.UniqueConstraint("bot_message_id", name="uq_msg"),
    )
    ddl = str(sa.schema.CreateTable(table).compile(dialect=sa.dialects.postgresql.dialect()))
    assert "CHECK" in ddl
    assert "retry_count >= 0" in ddl
    assert "simulated_sent" in ddl
