# -*- coding: utf-8 -*-

import os
import tempfile
import uuid

# Fuerza entorno de pruebas aislado para que pytest nunca use la BD real.
os.environ["APP_ENV"] = "test"
# Usa una base SQLite única por sesión para evitar colisiones entre corridas
# y bootstrap duplicado de tablas al reutilizar el mismo archivo temporal.
_tmp_db = os.path.join(tempfile.gettempdir(), f"app_web_pytest_{uuid.uuid4().hex}.sqlite")
os.environ["DATABASE_URL_TEST"] = f"sqlite:///{_tmp_db}"
os.environ.setdefault("DATABASE_URL", "postgresql://prod-user:prod-pass@prod-host/prod_db")

from app import app as flask_app
from config_app import db
from models import Cliente
from tests.t1_testkit import ensure_sqlite_compat_tables


def pytest_sessionstart(session):
    # Bootstrap mínimo y determinista para suites que usan Cliente sin migraciones.
    with flask_app.app_context():
        ensure_sqlite_compat_tables([Cliente], reset=False)
        # SQLite no autoincrementa BigInteger PK como se espera para algunos modelos;
        # normalizamos esta tabla de presencia solo en entorno de tests.
        cols = db.session.execute(db.text("PRAGMA table_info('staff_presence_state')")).fetchall()
        recreate_presence = False
        if cols:
            for col in cols:
                # pragma table_info: cid, name, type, notnull, dflt_value, pk
                if str(col[1]) == "id":
                    col_type = str(col[2] or "").strip().upper()
                    col_pk = int(col[5] or 0)
                    recreate_presence = not (col_type == "INTEGER" and col_pk == 1)
                    break
        if recreate_presence:
            db.session.execute(db.text("DROP TABLE IF EXISTS staff_presence_state"))

        db.session.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS staff_presence_state (
                  id INTEGER PRIMARY KEY,
                  user_id INTEGER NOT NULL,
                  session_id TEXT NOT NULL,
                  route TEXT NOT NULL DEFAULT '',
                  route_label TEXT NOT NULL DEFAULT '',
                  entity_type TEXT NOT NULL DEFAULT '',
                  entity_id TEXT NOT NULL DEFAULT '',
                  entity_name TEXT NOT NULL DEFAULT '',
                  entity_code TEXT NOT NULL DEFAULT '',
                  current_action TEXT NOT NULL DEFAULT '',
                  action_label TEXT NOT NULL DEFAULT '',
                  tab_visible BOOLEAN NOT NULL DEFAULT 1,
                  is_idle BOOLEAN NOT NULL DEFAULT 0,
                  is_typing BOOLEAN NOT NULL DEFAULT 0,
                  has_unsaved_changes BOOLEAN NOT NULL DEFAULT 0,
                  modal_open BOOLEAN NOT NULL DEFAULT 0,
                  lock_owner TEXT NOT NULL DEFAULT '',
                  client_status TEXT NOT NULL DEFAULT 'active',
                  page_title TEXT NOT NULL DEFAULT '',
                  last_interaction_at DATETIME,
                  state_hash TEXT NOT NULL DEFAULT '',
                  ip TEXT,
                  user_agent TEXT,
                  started_at DATETIME NOT NULL,
                  last_seen_at DATETIME NOT NULL,
                  updated_at DATETIME NOT NULL
                )
                """
            )
        )
        db.session.execute(
            db.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_staff_presence_user_session "
                "ON staff_presence_state(user_id, session_id)"
            )
        )
        db.session.commit()
