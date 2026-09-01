# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text, create_engine, inspect, text


MIGRATION_PATH = Path("migrations/versions/20260831_1200_create_entrevista_referencias.py").resolve()


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migration_20260831_1200_create_entrevista_referencias", str(MIGRATION_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_id_contract_and_graph_single_head():
    migration = _load_migration_module()
    assert migration.revision == "20260831_1200_refs"
    assert len(migration.revision) <= 32
    assert migration.down_revision == "20260709_1200"

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script.get_heads()
    assert heads == ["20260831_1200_refs"]
    assert all(rev.revision != "20260831_1200_create_entrevista_referencias" for rev in script.walk_revisions())


def _create_base_schema(conn):
    md = MetaData()
    Table("candidatas", md, Column("fila", Integer, primary_key=True), Column("referencias_laboral", Text), Column("referencias_familiares", Text))
    Table("entrevistas", md, Column("id", Integer, primary_key=True), Column("candidata_id", Integer, nullable=False), Column("tipo", String(30), nullable=False), Column("estado", String(20), nullable=False), Column("creada_en", DateTime, nullable=False))
    Table("entrevista_preguntas", md, Column("id", Integer, primary_key=True), Column("clave", String(120), nullable=False), Column("texto", String(255), nullable=False), Column("tipo", String(30), nullable=False), Column("opciones", Text), Column("orden", Integer, nullable=False), Column("activa", Boolean, nullable=False), Column("creada_en", DateTime, nullable=True))
    Table("entrevista_respuestas", md, Column("id", Integer, primary_key=True), Column("entrevista_id", Integer, ForeignKey("entrevistas.id")), Column("pregunta_id", Integer, nullable=False), Column("respuesta", Text), Column("creada_en", DateTime), Column("actualizada_en", DateTime))
    md.create_all(conn)


def test_migracion_entrevista_referencias_upgrade_backfill_y_downgrade_local_sqlite():
    tmp_db = Path(tempfile.gettempdir()) / "app_web_entrevista_referencias_migration.sqlite"
    if tmp_db.exists():
        tmp_db.unlink()
    engine = create_engine(f"sqlite:///{tmp_db}")
    migration = _load_migration_module()

    with engine.begin() as conn:
        _create_base_schema(conn)
        conn.execute(text("INSERT INTO candidatas (fila, referencias_laboral, referencias_familiares) VALUES (1, 'FORM-LAB', 'FORM-FAM')"))
        conn.execute(text("INSERT INTO candidatas (fila, referencias_laboral, referencias_familiares) VALUES (2, 'HIST-LAB', 'HIST-FAM')"))
        conn.execute(text("INSERT INTO entrevistas (id, candidata_id, tipo, estado, creada_en) VALUES (10, 1, 'domestica', 'completa', CURRENT_TIMESTAMP)"))
        conn.execute(text("INSERT INTO entrevistas (id, candidata_id, tipo, estado, creada_en) VALUES (20, 2, 'domestica', 'completa', CURRENT_TIMESTAMP)"))
        conn.execute(text("INSERT INTO entrevista_preguntas (id, clave, texto, tipo, opciones, orden, activa) VALUES (101, 'domestica.referencia_laboral', 'Ref laboral', 'texto', NULL, 1, 1)"))
        conn.execute(text("INSERT INTO entrevista_preguntas (id, clave, texto, tipo, opciones, orden, activa) VALUES (102, 'domestica.referencia_familiar', 'Ref familiar', 'texto', NULL, 2, 1)"))
        conn.execute(text("INSERT INTO entrevista_respuestas (id, entrevista_id, pregunta_id, respuesta, creada_en, actualizada_en) VALUES (1001, 10, 101, 'LAB-OLD', CURRENT_TIMESTAMP, NULL)"))
        conn.execute(text("INSERT INTO entrevista_respuestas (id, entrevista_id, pregunta_id, respuesta, creada_en, actualizada_en) VALUES (1002, 10, 101, 'LAB-NEW', CURRENT_TIMESTAMP, NULL)"))
        conn.execute(text("INSERT INTO entrevista_respuestas (id, entrevista_id, pregunta_id, respuesta, creada_en, actualizada_en) VALUES (1003, 10, 102, 'FAM-1', CURRENT_TIMESTAMP, NULL)"))
        conn.execute(text("INSERT INTO entrevista_respuestas (id, entrevista_id, pregunta_id, respuesta, creada_en, actualizada_en) VALUES (1004, 20, 101, 'HIST-LAB-ONLY', CURRENT_TIMESTAMP, NULL)"))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()

        insp = inspect(conn)
        assert "entrevista_referencias" in insp.get_table_names()
        rows = conn.execute(text("SELECT entrevista_id, tipo, texto FROM entrevista_referencias ORDER BY entrevista_id, tipo")).fetchall()
        assert rows == [(10, "familiar", "FAM-1"), (10, "laboral", "LAB-NEW"), (20, "laboral", "HIST-LAB-ONLY")]
        assert conn.execute(text("SELECT referencias_laboral, referencias_familiares FROM candidatas WHERE fila = 1")).fetchone() == ("FORM-LAB", "FORM-FAM")
        assert conn.execute(text("SELECT referencias_laboral, referencias_familiares FROM candidatas WHERE fila = 2")).fetchone() == ("HIST-LAB", "HIST-FAM")

        with Operations.context(ctx):
            migration.downgrade()

        insp = inspect(conn)
        assert "entrevista_referencias" not in insp.get_table_names()
        assert conn.execute(text("SELECT referencias_laboral, referencias_familiares FROM candidatas WHERE fila = 1")).fetchone() == ("FORM-LAB", "FORM-FAM")
        assert conn.execute(text("SELECT referencias_laboral, referencias_familiares FROM candidatas WHERE fila = 2")).fetchone() == ("HIST-LAB", "HIST-FAM")

    engine.dispose()
