#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from typing import Tuple

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import app as flask_app
from config_app import db


SQL_COUNT_NULL = text("SELECT count(*) FROM domain_outbox WHERE relay_status IS NULL")

SQL_BATCH_UPDATE = text(
    """
    WITH chunk AS (
        SELECT id
        FROM domain_outbox
        WHERE relay_status IS NULL
        ORDER BY id
        LIMIT :batch_size
        FOR UPDATE SKIP LOCKED
    )
    UPDATE domain_outbox d
    SET relay_status = CASE
        WHEN d.published_at IS NOT NULL THEN 'published'
        ELSE 'pending'
    END
    FROM chunk
    WHERE d.id = chunk.id
    RETURNING d.id
    """
)


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(msg: str) -> None:
    print(f"[{_ts()}] {msg}")


def _assert_postgres() -> None:
    bind = db.session.get_bind()
    dialect = (getattr(bind.dialect, "name", "") or "").strip().lower()
    if dialect != "postgresql":
        raise RuntimeError(
            "Este script requiere PostgreSQL (usa FOR UPDATE SKIP LOCKED). "
            f"Dialect detectado: '{dialect or 'unknown'}'. Abortando."
        )


def _count_nulls() -> int:
    return int(db.session.execute(SQL_COUNT_NULL).scalar() or 0)


def _run_batch(batch_size: int) -> int:
    try:
        result = db.session.execute(SQL_BATCH_UPDATE, {"batch_size": int(batch_size)})
        rows = result.fetchall()
        db.session.commit()
        return len(rows)
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RuntimeError(f"Fallo en batch de backfill (rollback aplicado): {exc}") from exc
    except Exception as exc:
        db.session.rollback()
        raise RuntimeError(f"Error inesperado en batch (rollback aplicado): {exc}") from exc


def backfill(batch_size: int, sleep_ms: int, max_batches: int, dry_run: bool) -> Tuple[int, int]:
    _assert_postgres()

    started_nulls = _count_nulls()
    _log(
        f"Inicio backfill relay_status "
        f"(batch_size={batch_size}, sleep_ms={sleep_ms}, max_batches={max_batches}, dry_run={dry_run})"
    )
    _log(f"Pendientes iniciales relay_status IS NULL: {started_nulls}")

    if dry_run:
        _log("Dry-run: no se realizaron actualizaciones.")
        return started_nulls, started_nulls

    total_updated = 0
    batches = 0
    run_started = time.time()

    while True:
        if max_batches > 0 and batches >= max_batches:
            _log(f"Se alcanzó max_batches={max_batches}.")
            break

        updated = _run_batch(batch_size=batch_size)
        batches += 1
        total_updated += updated

        if updated == 0:
            _log("No hay más filas para actualizar en esta corrida.")
            break

        remaining = _count_nulls()
        elapsed = max(0.0, time.time() - run_started)
        rate = (total_updated / elapsed) if elapsed > 0 else 0.0
        _log(
            f"Batch #{batches}: updated={updated}, total_updated={total_updated}, "
            f"remaining_nulls={remaining}, rate={rate:.1f} rows/s"
        )

        if sleep_ms > 0:
            time.sleep(max(0, sleep_ms) / 1000.0)

    ended_nulls = _count_nulls()
    elapsed_total = max(0.0, time.time() - run_started)
    _log(
        f"Fin corrida: batches={batches}, total_updated={total_updated}, "
        f"nulls_before={started_nulls}, nulls_after={ended_nulls}, elapsed_sec={elapsed_total:.1f}"
    )
    return started_nulls, ended_nulls


def main():
    parser = argparse.ArgumentParser(
        description="Backfill seguro por chunks para domain_outbox.relay_status (PostgreSQL)"
    )
    parser.add_argument("--batch-size", type=int, default=5000, help="Tamaño de lote (default: 5000)")
    parser.add_argument("--sleep-ms", type=int, default=100, help="Pausa entre lotes en ms (default: 100)")
    parser.add_argument(
        "--max-batches",
        type=int,
        default=0,
        help="Máximo de lotes por corrida (0 = sin límite, default: 0)",
    )
    parser.add_argument("--dry-run", action="store_true", help="No actualiza; solo reporta pendientes")
    args = parser.parse_args()

    if args.batch_size <= 0:
        raise SystemExit("batch-size debe ser > 0")
    if args.sleep_ms < 0:
        raise SystemExit("sleep-ms debe ser >= 0")
    if args.max_batches < 0:
        raise SystemExit("max-batches debe ser >= 0")

    with flask_app.app_context():
        before, after = backfill(
            batch_size=int(args.batch_size),
            sleep_ms=int(args.sleep_ms),
            max_batches=int(args.max_batches),
            dry_run=bool(args.dry_run),
        )
        if not args.dry_run and after > 0:
            _log("Backfill parcial: quedan filas pendientes. Re-ejecuta el script.")
        if not args.dry_run and after == 0:
            _log("Backfill completo: ya puedes aplicar la migración 20260328_1830.")


if __name__ == "__main__":
    main()
