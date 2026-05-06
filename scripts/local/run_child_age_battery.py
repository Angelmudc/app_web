#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from config_app import db
from models import Cliente, Solicitud
from utils.child_age_parser import has_child_age_five_or_less, parse_child_age_summary
from utils.sueldo_sugerido import analyze_salary_suggestion


@dataclass
class Case:
    label: str
    edades: str
    ninos: int


CASES: list[Case] = [
    # A. niños pequeños
    Case("A1 small: 2", "2", 1),
    Case("A2 small: 4", "4", 1),
    Case("A3 small: 2 y 4", "2 y 4", 2),
    Case("A4 small: 2, 4", "2, 4", 2),
    Case("A5 small: 1 año y 5 meses", "1 año y 5 meses", 1),
    Case("A6 small: 5 meses", "5 meses", 1),
    Case("A7 small: 2 años y 3 años", "2 años y 3 años", 2),
    Case("A8 small: 2 / 4", "2 / 4", 2),
    # B. niños grandes
    Case("B1 big: 6", "6", 1),
    Case("B2 big: 8", "8", 1),
    Case("B3 big: 6 y 8", "6 y 8", 2),
    Case("B4 big: 7, 10", "7, 10", 2),
    # C. adolescentes
    Case("C1 teen: 14", "14", 1),
    Case("C2 teen: 15 y 17", "15 y 17", 2),
    # D. mixtos
    Case("D1 mixed: 2, 7 y 14", "2, 7 y 14", 3),
    Case("D2 mixed: 4 y 15", "4 y 15", 2),
    Case("D3 mixed: 5 meses y 12", "5 meses y 12", 2),
    Case("D4 mixed: 3 años, 8 y 16", "3 años, 8 y 16", 3),
    # E. límite y texto raro
    Case("E1 edge: 18", "18", 1),
    Case("E2 edge: vacío", "", 1),
    Case("E3 edge: uno de 2 y otro de 14", "uno de 2 y otro de 14", 2),
    Case("E4 edge: bebé de 5 meses", "bebé de 5 meses", 1),
    Case("E5 edge: niño de 4", "niño de 4", 1),
]


def _must_be_local() -> None:
    env = (os.getenv("APP_ENV") or "").strip().lower()
    if env != "local":
        raise SystemExit(f"Abortado: APP_ENV debe ser 'local' y llegó '{env or '(vacío)'}'.")

    db_name = str(getattr(db.engine.url, "database", "") or "")
    if "domestica_cibao_local" not in db_name:
        raise SystemExit(
            "Abortado: la DB actual no parece ser 'domestica_cibao_local'. "
            f"database='{db_name}'"
        )


def _ensure_local_client() -> Cliente:
    client = Cliente.query.filter_by(email="local_battery_child_age@test.local").first()
    if client:
        return client
    client = Cliente(
        codigo="CL-LOCAL-BAT-AGE",
        nombre_completo="Cliente Local Battery Edad",
        email="local_battery_child_age@test.local",
        telefono="8095550000",
        username="local_battery_child_age",
        password_hash="DISABLED_RESET_REQUIRED",
        is_active=True,
        role="cliente",
        acepto_politicas=True,
        total_solicitudes=0,
    )
    db.session.add(client)
    db.session.commit()
    return client


def _salary_payload(edades: str, ninos: int) -> dict[str, Any]:
    return {
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "5:00 PM",
        "tipo_lugar": "casa",
        "habitaciones": "2",
        "banos": "2",
        "pisos": "1",
        "funciones": ["ninos", "limpieza"],
        "areas_comunes": [],
        "ninos": str(ninos),
        "edades_ninos": edades,
        "adultos": "2",
        "sueldo": "18000",
    }


def _expected_salary_delta(summary: dict[str, Any], ninos: int) -> int:
    small = int(summary.get("small_count") or 0)
    known_ages = bool(summary.get("ages_years"))
    if small > 0:
        return 1000 * small
    if ninos > 0 and not known_ages:
        # Comportamiento actual defensivo cuando no hay edades parseables.
        return 1000
    return 0


def run() -> int:
    with app.app_context():
        _must_be_local()
        client = _ensure_local_client()

        created_ids: list[int] = []
        rows: list[dict[str, Any]] = []
        errors: list[str] = []

        ts = datetime.now().strftime("%Y%m%d%H%M%S")

        for idx, case in enumerate(CASES, start=1):
            code = f"LOCAL-BAT-AGE-{ts}-{idx:03d}"
            try:
                summary = parse_child_age_summary(case.edades)
                small = int(summary.get("small_count") or 0)
                teen = int(summary.get("teen_count") or 0)
                alert = bool(
                    has_child_age_five_or_less(case.edades)
                    and ("ninos" in ["ninos", "limpieza"] and "limpieza" in ["ninos", "limpieza"])
                )

                salary = analyze_salary_suggestion(_salary_payload(case.edades, case.ninos))
                if not salary.get("can_suggest"):
                    raise RuntimeError(f"sin sugerencia: {salary.get('message')}")

                base = int(salary.get("base_salary") or 0)
                min_s = int(salary.get("suggested_min") or 0)
                delta = min_s - base
                expected_delta = _expected_salary_delta(summary, case.ninos)
                salary_ok = (delta == expected_delta)

                # Guardado completo local de solicitud sintética.
                solicitud = Solicitud(
                    cliente_id=client.id,
                    codigo_solicitud=code,
                    modalidad_trabajo="Salida diaria - lunes a viernes",
                    horario="Lunes a viernes, de 8:00 AM a 5:00 PM",
                    funciones=["ninos", "limpieza"],
                    tipo_lugar="casa",
                    habitaciones=2,
                    banos=2.0,
                    adultos=2,
                    ninos=case.ninos,
                    edades_ninos=case.edades,
                    sueldo="18000",
                    pasaje_aporte=True,
                    nota_cliente=f"local battery: {case.label}",
                )
                db.session.add(solicitud)
                db.session.commit()
                created_ids.append(int(solicitud.id))

                rows.append(
                    {
                        "case": case.label,
                        "edades": case.edades or "(vacío)",
                        "small_count": small,
                        "teen_count": teen,
                        "salary_delta": delta,
                        "expected_delta": expected_delta,
                        "salary_ok": salary_ok,
                        "alert": alert,
                        "saved": True,
                        "msg": (salary.get("message") or "").splitlines()[:2],
                    }
                )
                if not salary_ok:
                    errors.append(
                        f"{case.label}: delta sueldo esperado {expected_delta}, actual {delta}"
                    )
            except Exception as exc:
                db.session.rollback()
                errors.append(f"{case.label}: {exc}")
                rows.append(
                    {
                        "case": case.label,
                        "edades": case.edades or "(vacío)",
                        "small_count": None,
                        "teen_count": None,
                        "salary_delta": None,
                        "expected_delta": None,
                        "salary_ok": False,
                        "alert": None,
                        "saved": False,
                        "msg": [f"error: {exc}"],
                    }
                )

        # Limpieza de datos creados por la batería.
        if created_ids:
            Solicitud.query.filter(Solicitud.id.in_(created_ids)).delete(synchronize_session=False)
            db.session.commit()

        total = len(rows)
        saved = sum(1 for r in rows if r["saved"])
        salary_up = sum(1 for r in rows if isinstance(r["salary_delta"], int) and r["salary_delta"] > 0)
        salary_no_up = sum(1 for r in rows if isinstance(r["salary_delta"], int) and r["salary_delta"] == 0)
        alerts = sum(1 for r in rows if bool(r["alert"]))

        print("\n=== CHILD AGE BATTERY (LOCAL) ===")
        print(f"APP_ENV={os.getenv('APP_ENV')}")
        print(f"DB={getattr(db.engine.url, 'database', '')}")
        print(f"Total ejecutadas: {total}")
        print(f"Total guardadas: {saved}")
        print(f"Errores encontrados: {len(errors)}")
        print(f"Casos donde aumentó sueldo: {salary_up}")
        print(f"Casos donde NO aumentó sueldo: {salary_no_up}")
        print(f"Casos con alerta inteligente: {alerts}")

        print("\nDetalle por caso:")
        print("| # | Caso | Edades | small | teen | sueldo Δ | esperado Δ | alerta | guardada | ok sueldo |")
        print("|---|------|--------|-------|------|----------|------------|--------|----------|-----------|")
        for i, r in enumerate(rows, start=1):
            print(
                f"| {i} | {r['case']} | {r['edades']} | {r['small_count']} | {r['teen_count']} | "
                f"{r['salary_delta']} | {r['expected_delta']} | {r['alert']} | {r['saved']} | {r['salary_ok']} |"
            )

        print("\nEjemplos reales de salida (message):")
        examples = [r for r in rows if r["saved"]][:3]
        for ex in examples:
            print(f"- {ex['case']}: {' / '.join(ex['msg'])}")

        if errors:
            print("\nErrores:")
            for err in errors:
                print(f"- {err}")
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(run())
