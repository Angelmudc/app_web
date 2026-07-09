#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Medicion local, solo lectura, de queries criticas de tienda.

Uso recomendado:
  APP_ENV=local venv/bin/python scripts/local/measure_store_queries.py
  APP_ENV=local venv/bin/python scripts/local/measure_store_queries.py --explain
  APP_ENV=local venv/bin/python scripts/local/measure_store_queries.py --explain-analyze
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_app import create_app, db
from models import Candidata, CandidataWeb
from utils.guards import candidatas_activas_filter


def _compile_sql(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _explain_prefix(analyze: bool) -> str:
    if analyze:
        return "EXPLAIN (ANALYZE, BUFFERS, VERBOSE, FORMAT TEXT) "
    return "EXPLAIN (VERBOSE, FORMAT TEXT) "


def _run_explain(stmt, *, analyze: bool) -> list[str]:
    sql = _compile_sql(stmt)
    explain_sql = _explain_prefix(analyze) + sql
    rows = db.session.execute(text(explain_sql)).fetchall()
    return [str(row[0]) for row in rows]


def _base_public_query():
    return (
        db.session.query(Candidata, CandidataWeb)
        .join(CandidataWeb, Candidata.fila == CandidataWeb.candidata_id)
        .filter(CandidataWeb.visible.is_(True))
        .filter(CandidataWeb.estado_publico == "disponible")
    )


def _base_cliente_query():
    return (
        db.session.query(Candidata, CandidataWeb)
        .join(CandidataWeb, Candidata.fila == CandidataWeb.candidata_id)
        .filter(candidatas_activas_filter(Candidata))
        .filter(CandidataWeb.visible.is_(True))
        .filter(CandidataWeb.estado_publico == "disponible")
    )


def _listing_order(query):
    return query.order_by(
        db.case((CandidataWeb.orden_lista.is_(None), 1), else_=0).asc(),
        CandidataWeb.orden_lista.asc(),
        CandidataWeb.fecha_ultima_actualizacion.desc(),
        Candidata.fila.desc(),
    )


def _cliente_listing_order(query):
    return query.order_by(
        db.case((CandidataWeb.orden_lista.is_(None), 1), else_=0).asc(),
        CandidataWeb.orden_lista.asc(),
        Candidata.nombre_completo.asc(),
    )


def _count_stmt(query):
    subq = query.order_by(None).statement.subquery()
    return select(func.count()).select_from(subq)


def _public_distinct_ciudad_stmt():
    return (
        db.session.query(func.trim(CandidataWeb.ciudad_publica).label("value"))
        .filter(CandidataWeb.visible.is_(True))
        .filter(CandidataWeb.estado_publico == "disponible")
        .filter(CandidataWeb.ciudad_publica.isnot(None))
        .filter(func.trim(CandidataWeb.ciudad_publica) != "")
        .distinct()
        .order_by("value")
        .statement
    )


def _public_distinct_modalidad_stmt():
    return (
        db.session.query(func.trim(CandidataWeb.modalidad_publica).label("value"))
        .filter(CandidataWeb.visible.is_(True))
        .filter(CandidataWeb.estado_publico == "disponible")
        .filter(CandidataWeb.modalidad_publica.isnot(None))
        .filter(func.trim(CandidataWeb.modalidad_publica) != "")
        .distinct()
        .order_by("value")
        .statement
    )


def _detail_stmt(candidata_id: int = 1):
    return (
        db.session.query(Candidata, CandidataWeb)
        .join(CandidataWeb, Candidata.fila == CandidataWeb.candidata_id)
        .filter(Candidata.fila == int(candidata_id))
        .filter(CandidataWeb.visible.is_(True))
        .filter(CandidataWeb.estado_publico == "disponible")
        .statement
    )


def _print_block(title: str, lines: Iterable[str]) -> None:
    print(f"\n=== {title} ===")
    for line in lines:
        print(line)


def _sample_existing_id() -> int:
    row = (
        db.session.query(CandidataWeb.candidata_id)
        .filter(CandidataWeb.candidata_id.isnot(None))
        .order_by(CandidataWeb.candidata_id.asc())
        .first()
    )
    return int(row[0]) if row and row[0] else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Mide queries criticas de tienda.")
    parser.add_argument("--explain", action="store_true", help="Ejecuta EXPLAIN.")
    parser.add_argument("--explain-analyze", action="store_true", help="Ejecuta EXPLAIN ANALYZE.")
    args = parser.parse_args()

    analyze = bool(args.explain_analyze)
    explain = bool(args.explain or analyze)

    app = create_app()
    with app.app_context():
        row_count_exact = db.session.query(CandidataWeb).count()
        row_count_estimate = db.session.execute(
            text(
                """
                select reltuples::bigint
                from pg_class
                where oid = 'public.candidatas_web'::regclass
                """
            )
        ).scalar()

        indexes = db.session.execute(
            text(
                """
                select indexname, indexdef
                from pg_indexes
                where schemaname = 'public' and tablename = 'candidatas_web'
                order by indexname
                """
            )
        ).fetchall()

        sample_id = _sample_existing_id()

        public_base = _listing_order(_base_public_query())
        public_ciudad = _listing_order(_base_public_query().filter(CandidataWeb.ciudad_publica.ilike("%Santiago%")))
        public_modalidad = _listing_order(_base_public_query().filter(CandidataWeb.modalidad_publica.ilike("%Con dormida%")))
        private_base = _listing_order(_base_public_query())
        cliente_base = _cliente_listing_order(_base_cliente_query())

        stmts = [
            ("public_list_base", public_base.limit(12).offset(0).statement),
            ("public_list_count", _count_stmt(public_base)),
            ("public_list_ciudad_ilike", public_ciudad.limit(12).offset(0).statement),
            ("public_list_modalidad_ilike", public_modalidad.limit(12).offset(0).statement),
            ("public_distinct_ciudad", _public_distinct_ciudad_stmt()),
            ("public_distinct_modalidad", _public_distinct_modalidad_stmt()),
            ("private_list_base", private_base.limit(12).offset(0).statement),
            ("private_list_count", _count_stmt(private_base)),
            ("cliente_list_base", cliente_base.limit(12).offset(0).statement),
            ("cliente_list_count", _count_stmt(cliente_base)),
            ("detail_by_candidata_id", _detail_stmt(sample_id)),
        ]

        print(json.dumps(
            {
                "engine": db.engine.dialect.name,
                "database_url": db.engine.url.render_as_string(hide_password=True),
                "candidatas_web_count_exact": int(row_count_exact),
                "candidatas_web_count_estimate": int(row_count_estimate or 0),
                "sample_candidata_id": int(sample_id),
                "explain": explain,
                "analyze": analyze,
            },
            indent=2,
            ensure_ascii=True,
        ))

        _print_block(
            "candidatas_web_indexes",
            [f"{row.indexname}: {row.indexdef}" for row in indexes],
        )

        for name, stmt in stmts:
            _print_block(f"sql::{name}", [_compile_sql(stmt)])
            if explain:
                _print_block(f"explain::{name}", _run_explain(stmt, analyze=analyze))


if __name__ == "__main__":
    main()
