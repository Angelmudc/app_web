from __future__ import annotations

import argparse
import time

from app import app as flask_app
from config_app import db
from models import Solicitud
from services.solicitud_atractivo_service import apply_solicitud_atractivo_to_model
from utils.timezone import utc_now_naive


def _log(message: str) -> None:
    print(f"[backfill_solicitud_atractivo] {message}", flush=True)


def backfill(*, batch_size: int, sleep_ms: int, max_rows: int) -> int:
    processed = 0
    last_id = 0
    while True:
        q = (
            Solicitud.query
            .filter(Solicitud.id > int(last_id))
            .order_by(Solicitud.id.asc())
            .limit(int(batch_size))
        )
        rows = q.all()
        if not rows:
            break
        now_ref = utc_now_naive()
        for solicitud in rows:
            apply_solicitud_atractivo_to_model(solicitud, now=now_ref)
            last_id = int(getattr(solicitud, "id", 0) or 0)
            processed += 1
            if max_rows > 0 and processed >= max_rows:
                break
        db.session.commit()
        _log(f"Procesadas {processed} solicitudes. Último id={last_id}.")
        if max_rows > 0 and processed >= max_rows:
            break
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)
    return processed


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill de score de atractivo en solicitudes.")
    parser.add_argument("--batch-size", type=int, default=500, help="Cantidad de solicitudes por lote.")
    parser.add_argument("--sleep-ms", type=int, default=0, help="Pausa entre lotes en milisegundos.")
    parser.add_argument("--max-rows", type=int, default=0, help="Límite opcional de filas a procesar.")
    args = parser.parse_args()

    if args.batch_size <= 0:
        raise SystemExit("batch-size debe ser > 0")
    if args.sleep_ms < 0:
        raise SystemExit("sleep-ms debe ser >= 0")
    if args.max_rows < 0:
        raise SystemExit("max-rows debe ser >= 0")

    with flask_app.app_context():
        total = backfill(
            batch_size=int(args.batch_size),
            sleep_ms=int(args.sleep_ms),
            max_rows=int(args.max_rows),
        )
        _log(f"Backfill completado. Total procesadas={total}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
